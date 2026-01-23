"""
Settings route for configuration management.
"""

from flask import Blueprint, render_template, request, session, jsonify, redirect, url_for
import yaml
from pathlib import Path

from bufferlab_deploy.segmentation_engine import SegmentationThresholds

bp = Blueprint('settings', __name__)


def get_thresholds_from_session() -> SegmentationThresholds:
    """Get thresholds from session or defaults."""
    if 'thresholds' in session:
        return SegmentationThresholds(**session['thresholds'])
    return SegmentationThresholds()


def save_thresholds_to_session(thresholds: SegmentationThresholds) -> None:
    """Save thresholds to session."""
    session['thresholds'] = {
        'high_eo_unit_cost': thresholds.high_eo_unit_cost,
        'high_eo_days_to_risk': thresholds.high_eo_days_to_risk,
        'constrained_lead_time': thresholds.constrained_lead_time,
        'constrained_confidence': thresholds.constrained_confidence,
        'long_lead_threshold': thresholds.long_lead_threshold,
        'long_lead_days_to_risk_min': thresholds.long_lead_days_to_risk_min,
        'build_ahead_stranding_pct': thresholds.build_ahead_stranding_pct,
        'shared_usage_threshold': thresholds.shared_usage_threshold,
        'committed_max_coverage_weeks': thresholds.committed_max_coverage_weeks,
        'likely_max_coverage_weeks': thresholds.likely_max_coverage_weeks,
        'exploratory_coverage_weeks': thresholds.exploratory_coverage_weeks,
        'transition_buffer_reduction_pct': thresholds.transition_buffer_reduction_pct,
    }
    session.modified = True


@bp.route('/settings')
def settings():
    """Settings page."""
    thresholds = get_thresholds_from_session()
    return render_template('settings.html', thresholds=thresholds)


@bp.route('/api/settings', methods=['POST'])
def save_settings():
    """Save settings from form."""
    try:
        data = request.json or request.form.to_dict()
        
        thresholds = SegmentationThresholds(
            high_eo_unit_cost=float(data.get('high_eo_unit_cost', 5000)),
            high_eo_days_to_risk=int(data.get('high_eo_days_to_risk', 90)),
            constrained_lead_time=int(data.get('constrained_lead_time', 45)),
            constrained_confidence=float(data.get('constrained_confidence', 0.70)),
            long_lead_threshold=int(data.get('long_lead_threshold', 60)),
            long_lead_days_to_risk_min=int(data.get('long_lead_days_to_risk_min', 180)),
            build_ahead_stranding_pct=float(data.get('build_ahead_stranding_pct', 0.30)),
            shared_usage_threshold=int(data.get('shared_usage_threshold', 1)),
            committed_max_coverage_weeks=int(data.get('committed_max_coverage_weeks', 6)),
            likely_max_coverage_weeks=int(data.get('likely_max_coverage_weeks', 2)),
            exploratory_coverage_weeks=int(data.get('exploratory_coverage_weeks', 0)),
            transition_buffer_reduction_pct=float(data.get('transition_buffer_reduction_pct', 0.33)),
        )
        
        save_thresholds_to_session(thresholds)
        
        return jsonify({'success': True, 'message': 'Settings saved successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@bp.route('/api/settings/reset', methods=['POST'])
def reset_settings():
    """Reset to default settings."""
    session.pop('thresholds', None)
    session.modified = True
    return jsonify({'success': True, 'message': 'Settings reset to defaults'})


@bp.route('/api/settings/export', methods=['GET'])
def export_settings():
    """Export current settings as YAML."""
    thresholds = get_thresholds_from_session()
    
    settings_dict = {
        'segmentation_thresholds': {
            'high_eo_unit_cost': thresholds.high_eo_unit_cost,
            'high_eo_days_to_risk': thresholds.high_eo_days_to_risk,
            'constrained_lead_time': thresholds.constrained_lead_time,
            'constrained_confidence': thresholds.constrained_confidence,
            'long_lead_threshold': thresholds.long_lead_threshold,
            'long_lead_days_to_risk_min': thresholds.long_lead_days_to_risk_min,
            'build_ahead_stranding_pct': thresholds.build_ahead_stranding_pct,
            'shared_usage_threshold': thresholds.shared_usage_threshold,
        },
        'buffer_policy': {
            'committed_max_coverage_weeks': thresholds.committed_max_coverage_weeks,
            'likely_max_coverage_weeks': thresholds.likely_max_coverage_weeks,
            'exploratory_coverage_weeks': thresholds.exploratory_coverage_weeks,
            'transition_buffer_reduction_pct': thresholds.transition_buffer_reduction_pct,
        }
    }
    
    yaml_content = yaml.dump(settings_dict, default_flow_style=False)
    
    from flask import Response
    return Response(
        yaml_content,
        mimetype='application/x-yaml',
        headers={'Content-Disposition': 'attachment; filename=settings.yml'}
    )
