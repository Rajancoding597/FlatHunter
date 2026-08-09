from __future__ import annotations

from html import escape
from typing import Any


CORE_LABELS = {
    'listing type': 'property or room type',
    'home configuration': 'home configuration',
    'location': 'preferred location',
    'maximum budget': 'maximum monthly budget',
    'move-in timing': 'move-in timing',
}

FIELD_LABELS = {
    'listing_types': 'Rental arrangement',
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

CONFIGURATION_ANSWERED_MARKER = '__flathunter_configuration_answered'


def _configuration_answered(values: dict) -> bool:
    additional = values.get('additional_preferences') or {}
    marker = (
        additional.get(CONFIGURATION_ANSWERED_MARKER)
        if isinstance(additional, dict)
        else None
    )
    return bool(values.get('configuration_answered')) or bool(
        values.get('preferred_property_configurations')
    ) or str(marker or '').casefold() == 'true'


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
    if (
        'ENTIRE_PROPERTY' in (values.get('listing_types') or [])
        and not _configuration_answered(values)
    ):
        missing.append('home configuration')
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
        'home configuration': 'Which home configurations work for you, such as 1BHK, 2BHK, or Any?',
        'location': 'Which Hyderabad areas would you prefer?',
        'maximum budget': 'What is the maximum monthly rent you are comfortable with?',
        'move-in timing': 'When would you like to move in?',
    }
    return questions[missing[0]]


def format_requirements(
    requirements: Any,
    *,
    title: str = 'What I have so far',
    pending_change: Any = None,
) -> str:
    values = as_requirement_dict(requirements)
    lines = [f'<b>{escape(title)}</b>']
    visible = False
    for field in FIELD_LABELS:
        value = values.get(field)
        if field == 'additional_preferences' and isinstance(value, dict):
            value = {
                key: item
                for key, item in value.items()
                if not str(key).startswith('__flathunter_')
            }
        if (
            field == 'preferred_property_configurations'
            and _configuration_answered(values)
            and not value
        ):
            value = ['Any']
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
    if pending_change:
        pending = as_requirement_dict(pending_change)
        field = str(pending.get('field') or '').split('.')[-1]
        label = FIELD_LABELS.get(field, field.replace('_', ' ').title())
        lines.extend([
            '',
            '<b>Pending confirmation</b>',
            f'<b>{escape(label)} ? current:</b> {escape(_plain_pending_value(pending.get("current_value")))}',
            f'<b>{escape(label)} ? proposed:</b> {escape(_plain_pending_value(pending.get("proposed_value")))}',
        ])
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
            if str(key).startswith('__flathunter_'):
                continue
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


def _plain_pending_value(value: Any) -> str:
    if value in (None, '', [], {}):
        return 'Not set'
    if isinstance(value, dict):
        return ', '.join(
            f'{_humanize(key)}: {_humanize(item)}'
            for key, item in value.items()
            if item not in (None, '', [], {})
        ) or 'Not set'
    if isinstance(value, list):
        return ', '.join(_humanize(item) for item in value)
    return _humanize(value)
