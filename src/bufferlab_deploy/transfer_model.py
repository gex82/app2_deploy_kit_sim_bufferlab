"""
Multi-echelon Transfer Model.

Models inventory transfers from upstream nodes (integration/regional) to site nodes
with transfer lead times.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from bufferlab_deploy.duckdb_loader import DuckDBLoader
from bufferlab_deploy.config import get_config
from bufferlab_deploy.sql_utils import get_supply_week_expr


class TransferModel:
    """
    Models inventory movement through the supply chain network.
    """
    
    def __init__(self, loader: DuckDBLoader):
        self.loader = loader
        self.config = get_config()
    
    def get_lane_master(self) -> pl.DataFrame:
        """Get lane master data."""
        if not self.loader.loaded_tables.get("lane_master"):
            return pl.DataFrame()
        return self.loader.get_table("lane_master")
    
    def get_node_hierarchy(self) -> pl.DataFrame:
        """
        Get node hierarchy with site mappings.
        
        Returns:
            DataFrame with [node_id, site_id, node_type, region]
        """
        return self.loader.query("""
            SELECT 
                node_id,
                site_id,
                node_type,
                region
            FROM node_master
            ORDER BY site_id, node_type
        """)
    
    def get_inventory_by_node(self, as_of_date: date | None = None) -> pl.DataFrame:
        """
        Get current inventory by node.
        
        Returns:
            DataFrame with [node_id, site_id, node_type, item_id, usable_on_hand, unit_cost]
        """
        result = self.loader.query("""
            SELECT 
                ip.node_id,
                nm.site_id,
                nm.node_type,
                ip.item_id,
                ip.usable_on_hand,
                COALESCE(ip.unit_cost, im.unit_cost, 0) as unit_cost
            FROM inventory_position ip
            JOIN node_master nm ON ip.node_id = nm.node_id
            LEFT JOIN item_master im ON ip.item_id = im.item_id
            ORDER BY nm.site_id, nm.node_type, ip.item_id
        """)
        return result
    
    def get_supply_arrivals(self) -> pl.DataFrame:
        """
        Get scheduled supply arrivals by node/week.
        
        Returns:
            DataFrame with [week, node_id, site_id, node_type, item_id, arriving_qty]
        """
        week_expr = get_supply_week_expr(self.loader)
        if week_expr == "NULL":
            return pl.DataFrame()

        result = self.loader.query(f"""
            SELECT 
                {week_expr} as week,
                s.node_id,
                nm.site_id,
                nm.node_type,
                s.item_id,
                SUM(s.qty) as arriving_qty,
                AVG(COALESCE(s.confidence_weight, 1.0)) as avg_confidence
            FROM supply s
            JOIN node_master nm ON s.node_id = nm.node_id
            WHERE s.status NOT IN ('cancelled', 'received')
            GROUP BY {week_expr}, s.node_id, nm.site_id, nm.node_type, s.item_id
            ORDER BY week, nm.site_id, s.item_id
        """)
        return result
    
    def calculate_arrivals_to_site(self, weeks: list[date]) -> pl.DataFrame:
        """
        Calculate total arrivals to each site by week including transfers.
        
        For each site/item/week:
        - Direct arrivals at site nodes
        - Transferred arrivals from upstream (shifted by lead time)
        
        Args:
            weeks: List of week-start dates
        
        Returns:
            DataFrame with [week, site_id, item_id, direct_arrivals, transferred_arrivals, total_arrivals]
        """
        if not weeks:
            return pl.DataFrame()
        
        # Get transfer settings
        transfers_enabled = self.config.analysis.transfers.enabled
        week_expr = get_supply_week_expr(self.loader)
        if week_expr == "NULL":
            return pl.DataFrame()
        
        if not transfers_enabled or not self.loader.loaded_tables.get("lane_master"):
            # Simple case: only direct arrivals at site
            result = self.loader.query(f"""
                SELECT 
                    {week_expr} as week,
                    nm.site_id,
                    s.item_id,
                    SUM(s.qty) as direct_arrivals,
                    0 as transferred_arrivals,
                    SUM(s.qty) as total_arrivals
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type = 'site'
                GROUP BY {week_expr}, nm.site_id, s.item_id
                ORDER BY week, site_id, item_id
            """)
            return result
        
        # Full transfer model
        min_week = min(weeks)
        min_week_sql = min_week if isinstance(min_week, str) else min_week.isoformat()
        if self.config.analysis.transfers.assume_unlimited_capacity:
            capacity_clause = ", NULL as lane_capacity"
        else:
            capacity_clause = ", lm.transfer_capacity_units_per_week as lane_capacity"

        result = self.loader.query(f"""
            WITH direct_supply AS (
                -- Supply arriving directly at site nodes
                SELECT 
                    {week_expr} as week,
                    nm.site_id,
                    s.item_id,
                    SUM(s.qty) as qty
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type = 'site'
                GROUP BY {week_expr}, nm.site_id, s.item_id
            ),
            upstream_supply AS (
                -- Supply arriving at upstream nodes (integration/regional)
                SELECT 
                    {week_expr} as receipt_week,
                    s.node_id,
                    nm.site_id,
                    nm.node_type,
                    s.item_id,
                    SUM(s.qty) as qty
                FROM supply s
                JOIN node_master nm ON s.node_id = nm.node_id
                WHERE s.status NOT IN ('cancelled', 'received')
                  AND nm.node_type IN ('integration', 'regional')
                GROUP BY {week_expr}, s.node_id, nm.site_id, nm.node_type, s.item_id
            ),
            upstream_inventory AS (
                -- Inventory at upstream nodes treated as available at min week
                SELECT 
                    DATE '{min_week_sql}' as receipt_week,
                    ip.node_id,
                    nm.site_id,
                    nm.node_type,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as qty
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_type IN ('integration', 'regional')
                GROUP BY ip.node_id, nm.site_id, nm.node_type, ip.item_id
            ),
            lanes_to_site AS (
                -- Get lanes that end at site nodes
                SELECT 
                    lm.from_node_id,
                    lm.to_node_id,
                    nm_to.site_id,
                    lm.transfer_lead_time_days
                    {capacity_clause}
                FROM lane_master lm
                JOIN node_master nm_to ON lm.to_node_id = nm_to.node_id
                WHERE nm_to.node_type = 'site'
            ),
            transferred_supply AS (
                -- Upstream supply shifted by transfer lead time
                SELECT 
                    us.receipt_week + INTERVAL (COALESCE(l.transfer_lead_time_days, 7)) DAY as week,
                    l.site_id,
                    us.item_id,
                    SUM(
                        CASE 
                            WHEN l.lane_capacity IS NULL THEN us.qty
                            WHEN l.lane_capacity = 0 THEN us.qty
                            ELSE LEAST(us.qty, l.lane_capacity)
                        END
                    ) as qty
                FROM (
                    SELECT * FROM upstream_supply
                    UNION ALL
                    SELECT * FROM upstream_inventory
                ) us
                JOIN lanes_to_site l ON us.node_id = l.from_node_id
                GROUP BY us.receipt_week + INTERVAL (COALESCE(l.transfer_lead_time_days, 7)) DAY, l.site_id, us.item_id
            )
            SELECT 
                COALESCE(d.week, t.week) as week,
                COALESCE(d.site_id, t.site_id) as site_id,
                COALESCE(d.item_id, t.item_id) as item_id,
                COALESCE(d.qty, 0) as direct_arrivals,
                COALESCE(t.qty, 0) as transferred_arrivals,
                COALESCE(d.qty, 0) + COALESCE(t.qty, 0) as total_arrivals
            FROM direct_supply d
            FULL OUTER JOIN transferred_supply t 
                ON d.week = t.week AND d.site_id = t.site_id AND d.item_id = t.item_id
            ORDER BY week, site_id, item_id
        """)
        
        return result
    
    def get_starting_inventory_at_site(self) -> pl.DataFrame:
        """
        Get starting inventory available at each site.
        
        Includes:
        - Inventory at site nodes (immediately available)
        - Inventory at upstream nodes (available after transfer lead time)
        
        Returns:
            DataFrame with [site_id, item_id, site_inventory, upstream_inventory]
        """
        if not self.loader.loaded_tables.get("lane_master"):
            # Simple case: only site inventory
            result = self.loader.query("""
                SELECT 
                    nm.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as site_inventory,
                    0 as upstream_inventory,
                    SUM(ip.usable_on_hand) as total_inventory
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_type = 'site'
                GROUP BY nm.site_id, ip.item_id
            """)
            return result
        
        # Full transfer model
        result = self.loader.query("""
            WITH site_inv AS (
                SELECT 
                    nm.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as qty
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                WHERE nm.node_type = 'site'
                GROUP BY nm.site_id, ip.item_id
            ),
            upstream_inv AS (
                SELECT 
                    l.site_id,
                    ip.item_id,
                    SUM(ip.usable_on_hand) as qty
                FROM inventory_position ip
                JOIN node_master nm ON ip.node_id = nm.node_id
                JOIN (
                    SELECT DISTINCT 
                        lm.from_node_id,
                        nm_to.site_id
                    FROM lane_master lm
                    JOIN node_master nm_to ON lm.to_node_id = nm_to.node_id
                    WHERE nm_to.node_type = 'site'
                ) l ON ip.node_id = l.from_node_id
                WHERE nm.node_type IN ('integration', 'regional')
                GROUP BY l.site_id, ip.item_id
            )
            SELECT 
                COALESCE(s.site_id, u.site_id) as site_id,
                COALESCE(s.item_id, u.item_id) as item_id,
                COALESCE(s.qty, 0) as site_inventory,
                COALESCE(u.qty, 0) as upstream_inventory,
                COALESCE(s.qty, 0) + COALESCE(u.qty, 0) as total_inventory
            FROM site_inv s
            FULL OUTER JOIN upstream_inv u ON s.site_id = u.site_id AND s.item_id = u.item_id
            ORDER BY site_id, item_id
        """)
        
        return result
