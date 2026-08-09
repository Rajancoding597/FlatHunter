from __future__ import annotations

from html import escape
from typing import Any


CORE_LABELS = {
    'listing type': 'property or room type',
    'location': 'preferred location',
    'maximum budget': 'maximum monthly budget',
    'move-in timing': 'move-in timing',
}

FIELD_LABELS = {
    'listing_types': 'Listing type',
    'preferred_locations': 'Preferred locations',
    'acceptable_locations': 'Also acceptable',
    'excluded_locations': 'Excluded locations',
    'work_location': 'Work location',
    'target_rent': 'Target rent',
    'max_rent': 'Maximum rent',
    'preferred_move_in_date': 'Preferred move-in',
    'latest_move_in_date': 'Latest move-in',
    'preferred_property_configurations': 'Configuration',
    'core_preferences': 'Preferences',
    'additional_preferences': 'Other preferences',
}


def as_requirement_dict(requirements: Any) -> dict:
    if requirements is None:
        return {}
    if hasattr(requirements, 'model_dump'):
        return requirements.model_dump(mode='json')
    return dict(requirements)


def missing_core_fields(requirements: Any) -> list[str]:
    values = as_requirement_dict(requirements)
    missing = []
    if not values.get('listing_types'):
        missing.append('listing type')
    if not (values.get('preferred_locations') or values.get('acceptable_locations')):
        missing.append('location')
    if not values.get('max_rent'):
        missing.append('maximum budget')
    if not (values.get('preferred_move_in_date') or values.get('latest_move_in_date')):
        missing.append('move-in timing')
    return missing


def next_requirement_question(requirements: Any) -> str:
    missing = missing_core_fields(requirements)
    if not missing:
        return 'Anything else you would like me to consider, or should I start searching?'
    questions = {
        'listing type': 'Are you looking for an entire flat, a private room, or a shared room?',
        'location': 'Which Hyderabad areas would you prefer?',
        'maximum budget': 'What is the maximum monthly rent you are comfortable with?',
        'move-in timing': 'When would you like to move in?',
    }
    return questions[missing[0]]


def format_requirements(requirements: Any, *, title: str = 'What I have so far') -> str:
    values = as_requirement_dict(requirements)
    lines = [f'<b>{escape(title)}</b>']
    visible = False
    for field in FIELD_LABELS:
        value = values.get(field)
        if value in (None, '', [], {}):
            continue
        visible = True
        lines.append(f'<b>{FIELD_LABELS[field]}:</b> {_format_value(field, value)}')
    if not visible:
        lines.append('I have not collected any search requirements yet.')

    missing = missing_core_fields(values)
    if missing:
        missing_text = ', '.join(CORE_LABELS[item] for item in missing)
        lines.extend(['', f'<b>Still needed:</b> {escape(missing_text)}'])
    else:
        lines.extend(['', '<b>Core requirements:</b> complete'])
    return '\n'.join(lines)


def format_requirement_diff(current: Any, proposed: Any) -> str:
    before = as_requirement_dict(current)
    after = as_requirement_dict(proposed)
    lines = ['<b>Proposed changes</b>']
    for field, label in FIELD_LABELS.items():
        if before.get(field) == after.get(field):
            continue
        old_value = _format_value(field, before.get(field)) if before.get(field) not in (None, '', [], {}) else 'Not set'
        new_value = _format_value(field, after.get(field)) if after.get(field) not in (None, '', [], {}) else 'Removed'
        lines.append(f'<b>{label}:</b> {old_value} → {new_value}')
    if len(lines) == 1:
        lines.append('No effective changes were found.')
    return '\n'.join(lines)


def _format_value(field: str, value: Any) -> str:
    if field in {'target_rent', 'max_rent'}:
        try:
            return f'₹{int(value):,} per month'
        except (TypeError, ValueError):
            return escape(str(value))
    if isinstance(value, list):
        return escape(', '.join(_humanize(item) for item in value))
    if field in {'core_preferences', 'additional_preferences'} and isinstance(value, dict):
        items = []
        for key, detail in value.items():
            if isinstance(detail, dict):
                raw = detail.get('value')
                importance = str(detail.get('importance') or '').split('.')[-1]
                suffix = ' (required)' if importance == 'REQUIRED' else ''
                shown = _humanize(key) if raw is True else f'{_humanize(key)}: {_humanize(raw)}'
                items.append(f'{shown}{suffix}')
            else:
                items.append(f'{_humanize(key)}: {_humanize(detail)}')
        return escape(', '.join(items))
    return escape(_humanize(value))


def _humanize(value: Any) -> str:
    if value is True:
        return 'yes'
    if value is False:
        return 'no'
    if value is None:
        return 'not set'
    return str(value).replace('_', ' ').strip().title()
