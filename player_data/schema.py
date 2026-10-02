"""Normalized domain fields; game-specific raw scales belong to adapters."""
import math
from copy import deepcopy
from player_data.models import PlayerDataError

ATTRIBUTE_FIELDS = {
    'offense': {'attacking_awareness', 'ball_control', 'dribbling', 'tight_possession', 'passing',
                'low_pass', 'lofted_pass', 'shooting', 'finishing', 'heading', 'place_kicking', 'curl'},
    'defense': {'defensive_awareness', 'ball_winning', 'aggression', 'tackling'},
    'physical': {'speed', 'acceleration', 'strength', 'stamina', 'jumping', 'balance', 'kicking_power', 'physical_contact'},
    'goalkeeping': {'gk_awareness', 'gk_reflexes', 'gk_catching', 'gk_clearing', 'gk_reach'},
}
FEATURE_LIMITS = {'weak_foot_frequency': (1, 5), 'weak_foot_accuracy': (1, 5),
                  'form_consistency': (1, 8), 'injury_resistance': (1, 5)}
PROFILE_TEXT = {'name', 'position', 'nationality', 'club', 'injury', 'preferred_foot',
                'contract_until', 'training_intensity', 'last_updated'}
PROFILE_NUMBERS = {'age': (0, 120), 'height': (1, 300), 'weight': (1, 500),
                   'overall': (0, 100), 'observed_market_value': (0, None)}
PROFILE_FIELDS = PROFILE_TEXT | set(PROFILE_NUMBERS) | {'attributes', 'other_features', 'injury_history', 'long_term_goals'}
TRAINING_FIELDS = {'week', 'date_range', 'focus', 'training_sessions', 'weekly_load', 'avg_rpe', 'notes'}
MATCH_FIELDS = {'date', 'opponent', 'competition', 'result', 'minutes_played', 'goals', 'assists', 'rating', 'position', 'key_stats', 'notes'}
CAREER_FIELDS = {'player_name', 'career_start', 'milestones', 'career_aspirations', 'contract_status', 'market_value_history', 'last_updated'}
KEY_STATS = {'shots', 'shots_on_target', 'passes', 'pass_accuracy', 'dribbles_attempted', 'dribbles_successful', 'sprint_count', 'distance_covered_km'}


def number(value, low=0, high=None, integer=False):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlayerDataError('invalid_number', 'Expected a finite domain number')
    if value < low or high is not None and value > high or integer and int(value) != value:
        raise PlayerDataError('invalid_number', 'Domain number is outside its declared scale')


def object_fields(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        raise PlayerDataError('invalid_fields', 'Unknown or invalid domain fields')


def text(value):
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 8000):
        raise PlayerDataError('invalid_text', 'Invalid domain text')


def validate_profile(value):
    object_fields(value, PROFILE_FIELDS)
    for key, item in value.items():
        if key in PROFILE_TEXT:
            text(item)
        elif key in PROFILE_NUMBERS:
            number(item, *PROFILE_NUMBERS[key], integer=key == 'age')
        elif key == 'attributes':
            object_fields(item, set(ATTRIBUTE_FIELDS))
            for category, attributes in item.items():
                object_fields(attributes, ATTRIBUTE_FIELDS[category])
                for amount in attributes.values():
                    number(amount, 0, 100)  # canonical scale, not arbitrary game raw attributes
        elif key == 'other_features':
            object_fields(item, set(FEATURE_LIMITS))
            for feature, amount in item.items():
                number(amount, *FEATURE_LIMITS[feature], integer=True)
        elif key in {'injury_history', 'long_term_goals'}:
            if item is not None and not isinstance(item, list) and not (key == 'long_term_goals' and isinstance(item, str)):
                raise PlayerDataError('invalid_field_type', 'Expected a domain list')
            if isinstance(item, str):
                text(item)
            elif isinstance(item, list):
                for entry in item:
                    if isinstance(entry, str):
                        text(entry)
                    elif key == 'injury_history' and isinstance(entry, dict):
                        object_fields(entry, {'date', 'injury', 'body_part', 'severity', 'status', 'duration_days', 'notes'})
                        if not entry.get('injury'):
                            raise PlayerDataError('invalid_record', 'Injury history requires an observed injury')
                        for field, amount in entry.items():
                            if field == 'duration_days':
                                number(amount, integer=True)
                            else:
                                text(amount)
                    else:
                        raise PlayerDataError('invalid_field_type', 'Invalid domain list entry')
    return deepcopy(value)


def validate_record(kind, value):
    if kind == 'profile_patch':
        return validate_profile(value)
    if kind == 'career_event':
        object_fields(value, {'date', 'event', 'overall_at_time', 'details'})
        if not value.get('event'):
            raise PlayerDataError('invalid_record', 'Career event requires an event description')
        number(value.get('overall_at_time'), 0, 100)
        for field in ('date', 'event', 'details'):
            if field in value:
                text(value[field])
        return deepcopy(value)
    object_fields(value, TRAINING_FIELDS if kind == 'training_record' else MATCH_FIELDS if kind == 'match_record' else set())
    if not value:
        raise PlayerDataError('invalid_record', 'Empty observation payload')
    numeric = {'weekly_load': (0, None), 'avg_rpe': (0, 10), 'minutes_played': (0, None),
               'goals': (0, None), 'assists': (0, None), 'rating': (0, 10)}
    for key, item in value.items():
        if key in numeric:
            number(item, *numeric[key], integer=key in {'goals', 'assists'})
        elif key == 'key_stats':
            if item is None:
                continue
            object_fields(item, KEY_STATS)
            for stat, amount in item.items():
                number(amount, 0, 100 if stat == 'pass_accuracy' else None,
                       integer=stat not in {'pass_accuracy', 'distance_covered_km'})
        elif key == 'training_sessions':
            if item is None:
                continue
            if not isinstance(item, list):
                raise PlayerDataError('invalid_record', 'Expected training sessions')
            for session in item:
                object_fields(session, {'day', 'type', 'duration_min', 'intensity'})
                number(session.get('duration_min'))
                for field in ('day', 'type', 'intensity'):
                    if field in session:
                        text(session[field])
        else:
            text(item)
    return deepcopy(value)


def project_fixture(profile):
    """Only explicit canonical fixture fields; ignored sample noise is reported."""
    object_fields(profile, set(profile))
    projected = {k: deepcopy(v) for k, v in profile.items() if k in PROFILE_FIELDS}
    ignored = sorted(set(profile) - PROFILE_FIELDS)
    for category, values in (projected.get('attributes') or {}).items():
        if category not in ATTRIBUTE_FIELDS or not isinstance(values, dict):
            raise PlayerDataError('corrupt_data', 'Invalid fixture attribute structure')
        ignored.extend('attributes.' + category + '.' + k for k in values if k not in ATTRIBUTE_FIELDS[category])
        projected['attributes'][category] = {k: v for k, v in values.items() if k in ATTRIBUTE_FIELDS[category]}
    if projected.get('other_features'):
        ignored.extend('other_features.' + k for k in projected['other_features'] if k not in FEATURE_LIMITS)
        projected['other_features'] = {k: v for k, v in projected['other_features'].items() if k in FEATURE_LIMITS}
    return validate_profile(projected), ignored


def validate_career(career, *, fixture=False):
    """Preserve estimates under estimate keys; never elevate them to observations."""
    if not isinstance(career, dict):
        raise PlayerDataError('invalid_fields', 'Invalid career projection')
    ignored = []
    def fields(value, allowed, prefix):
        if not isinstance(value, dict):
            raise PlayerDataError('invalid_fields', 'Invalid career container')
        unknown = set(value) - allowed
        if unknown and not fixture:
            raise PlayerDataError('invalid_fields', 'Unknown career fields')
        ignored.extend(prefix + '.' + key for key in sorted(unknown))
        return {key: deepcopy(item) for key, item in value.items() if key in allowed}
    result = fields(career, CAREER_FIELDS, 'career')
    for key in ('player_name', 'career_start', 'last_updated'):
        if key in result:
            text(result[key])
    for key, allowed in (
        ('career_aspirations', {'short_term', 'mid_term', 'long_term'}),
        ('contract_status', {'current_club', 'contract_type', 'expiry', 'release_clause'}),
    ):
        if key in result:
            result[key] = fields(result[key], allowed, 'career.' + key)
            for field, amount in result[key].items():
                number(amount) if field == 'release_clause' else text(amount)
    for key in ('milestones', 'market_value_history'):
        if key not in result:
            continue
        if not isinstance(result[key], list):
            raise PlayerDataError('invalid_fields', 'Invalid career record list')
        projected = []
        for index, row in enumerate(result[key]):
            allowed = {'date', 'event', 'overall_at_time', 'details'} if key == 'milestones' else {'date', 'estimated_value_eur', 'observed_value_eur'}
            row = fields(row, allowed, f'career.{key}.{index}')
            if key == 'milestones':
                validate_record('career_event', row)
            else:
                for field, amount in row.items():
                    text(amount) if field == 'date' else number(amount)
            projected.append(row)
        result[key] = projected
    return result, ignored
