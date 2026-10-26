"""Deterministic observed changes. No effectiveness score or causal attribution."""
from datetime import date
import math
from career_actions.assessment_models import METRICS


LIMITATIONS = [
    '观察变化不证明由建议造成；不生成建议有效率或成长收益分',
    '游戏操作支持仍待验证；执行反馈只代表用户自报',
    '现实提交时间不代表游戏顺序；不同位置/赛事/出场时间未做因果或公平优劣校正',
]


def day(row):
    if row.get('time_domain') != 'game' or row.get('time_precision') != 'day' or not row.get('effective_at'):
        raise ValueError('观察没有可排序的同一游戏日时间域')
    value = row['effective_at']
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('游戏日期格式不一致')
    return value


def metric_value(row, metric):
    value = row['payload']
    path = METRICS[metric]['path'].removeprefix('profile/')
    for part in path.split('/'):
        value = value.get(part) if isinstance(value, dict) else None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('观察指标不是规范有限数值')
    return value


def contains_metric(row, metric):
    value = row['payload']
    for part in METRICS[metric]['path'].removeprefix('profile/').split('/'):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def observation_reference(row, metric):
    return {key: row.get(key) for key in ('key', 'observation_id', 'provider', 'source_type',
        'source_record_id', 'game_version', 'adapter_version', 'semantic_hash', 'effective_at',
        'time_domain', 'time_precision', 'kind')} | {'value': metric_value(row, metric)}


def active_matches(bundle):
    keys = set(bundle['snapshot'].metadata.get('origins', {}).get(path)
        for path in bundle['snapshot'].metadata.get('origins', {}) if path.startswith('matches/'))
    return [row for row in bundle['observations'] if row['key'] in keys and row['kind'] == 'match_record' and row['import_status'] == 'APPLIED']


def anchor(bundle):
    keys = set(bundle['snapshot'].metadata.get('origins', {}).values())
    dates = []
    for row in bundle['observations']:
        if row['key'] in keys and row['import_status'] == 'APPLIED':
            try:
                dates.append(day(row))
            except ValueError:
                pass
    if not dates:
        raise ValueError('原快照没有已核验的游戏日期，不能指定后续窗口')
    return max(dates)


def freeze_baseline(bundle, spec, anchor_at):
    if METRICS[spec.metric]['kind'] == 'profile_patch':
        key = bundle['snapshot'].metadata.get('origins', {}).get(METRICS[spec.metric]['path'])
        rows = [row for row in bundle['observations'] if row['key'] == key and row['import_status'] == 'APPLIED']
    else:
        rows = active_matches(bundle)
        # Preserve unsupported times as evidence instead of silently dropping them.
        try:
            rows = sorted(rows, key=day)
            if len({day(row) for row in rows}) != len(rows):
                raise ValueError('基线同日多场比赛无法排序')
            rows = [row for row in rows if day(row) <= anchor_at][-spec.baseline_count:]
        except ValueError:
            return [observation_reference(row, spec.metric) for row in rows]
    return [observation_reference(row, spec.metric) for row in rows]


def corrected_baseline(plan, bundle):
    spec = plan.specification
    by_key = {row['key']: row for row in bundle['observations']}
    references = []
    for frozen in plan.baseline_observations:
        row = by_key.get(frozen['key'])
        if not row or observation_reference(row, spec.metric) != frozen:
            raise ValueError('原基线观察缺失或内容与冻结版本不一致')
        seen = set()
        while True:
            if row['key'] in seen:
                raise ValueError('观察更正关系成环')
            seen.add(row['key'])
            corrections = [item for item in bundle['observations'] if item.get('corrects_key') == row['key']
                and item['import_status'] == 'APPLIED' and item['kind'] == row['kind']
                and (METRICS[spec.metric]['kind'] != 'profile_patch' or contains_metric(item, spec.metric))]
            if not corrections:
                break
            if len(corrections) != 1 or day(corrections[0]) != day(row):
                raise ValueError('基线更正关系不明确或生效时间改变')
            row = corrections[0]
        references.append(observation_reference(row, spec.metric))
    return references


def calculate(plan, bundle, execution):
    spec, definition = plan.specification, plan.metric_definition
    result = dict(evidence_status='INSUFFICIENT', goal_status='UNDETERMINED',
        before=None, after=None, delta=None, baseline_samples=0, followup_samples=0,
        baseline_missing=0, followup_missing=0, window_matches=0, window_complete=False,
        baseline_observations=[], followup_observations=[], missing=[], exclusions={},
        unit=definition['unit'], scale=definition['scale'], method=definition['method'],
        time_domain='game', time_precision='day', execution=execution, limitations=list(LIMITATIONS), reason='等待足够的可比观察')
    result['exclusions']['recorded_only'] = sum(row['import_status'] == 'RECORDED_ONLY' for row in bundle['observations'])
    try:
        baseline = corrected_baseline(plan, bundle)
        for ref in baseline:
            day(ref)
        if len(baseline) > spec.baseline_count or definition['kind'] != 'profile_patch' and len({day(row) for row in baseline}) != len(baseline):
            raise ValueError('基线比赛顺序或样本边界不明确')
        result['baseline_observations'] = baseline
        if baseline != plan.baseline_observations:
            result['limitations'].append('原基线观察已有显式APPLIED更正；本版按更正来源计算，原冻结基线保留')
        matches = active_matches(bundle)
        # An active match without comparable chronology cannot be sorted past the anchor.
        for row in matches:
            day(row)
        matches = sorted([row for row in matches if day(row) > plan.anchor_at], key=day)
        if spec.window.kind == 'next_matches':
            selected = matches[:spec.window.count]
            if len({day(row) for row in matches[:spec.window.count + 1]}) != len(matches[:spec.window.count + 1]):
                raise ValueError('后续同日多场比赛无法确定前N场顺序')
            complete = len(selected) == spec.window.count
        else:
            selected = [row for row in matches if spec.window.start <= day(row) <= spec.window.end]
            complete = anchor(bundle) >= spec.window.end
        result['window_matches'], result['window_complete'] = len(selected), complete
        if definition['kind'] == 'profile_patch':
            rows = [row for row in bundle['observations'] if row['kind'] == 'profile_patch'
                and row['import_status'] == 'APPLIED' and contains_metric(row, spec.metric)]
            for row in rows:
                day(row)
            rows = [row for row in rows if spec.window.start <= day(row) <= spec.window.end]
            corrected_keys = {row['corrects_key'] for row in rows if row.get('corrects_key')}
            rows = [row for row in rows if row['key'] not in corrected_keys]
            rows.sort(key=day)
            if len(rows) > 1 and day(rows[-1]) == day(rows[-2]):
                raise ValueError('后续同日能力来源存在歧义')
            selected = rows[-1:]
        followup = [observation_reference(row, spec.metric) for row in selected]
        result['followup_observations'] = followup
        all_refs = baseline + followup
        if all_refs:
            versions = {(row['source_type'], row['provider'], row['game_version'], row['adapter_version']) for row in all_refs}
            if len(versions) != 1 or not all(row['game_version'] and row['adapter_version'] for row in all_refs):
                raise ValueError('来源provider、游戏/适配版本或量表依据不同/未知，默认不可比')
        before = [row['value'] for row in baseline if row['value'] is not None]
        after = [row['value'] for row in followup if row['value'] is not None]
        result.update(baseline_samples=len(before), followup_samples=len(after),
            baseline_missing=len(baseline) - len(before), followup_missing=len(followup) - len(after))
        if len(baseline) < spec.baseline_count:
            result['missing'].append('基线观察场数不足')
        if result['baseline_missing'] or result['followup_missing']:
            result['missing'].append('部分观察指标缺失；均值排除缺值，明确零值纳入')
        result['before'] = sum(before) / len(before) if before else None
        result['after'] = sum(after) / len(after) if after else None
        if before and after:
            result['delta'] = result['after'] - result['before']
        if not complete or len(baseline) < spec.baseline_count or min(len(before), len(after)) < spec.minimum_samples:
            result['reason'] = '窗口尚未完成、基线不足或有效样本数未达规范；不能判目标失败'
            return result
        result['evidence_status'], result['reason'] = 'COMPLETE', '按冻结规范完成观测比较；不代表建议有效'
        if spec.target_value is not None:
            met = result['after'] >= spec.target_value if spec.target_operator == 'gte' else result['after'] <= spec.target_value
            result['goal_status'] = 'MET' if met else 'NOT_MET'
        return result
    except ValueError as error:
        result.update(evidence_status='INCOMPARABLE', goal_status='UNDETERMINED', before=None, after=None, delta=None, reason=str(error))
        return result
