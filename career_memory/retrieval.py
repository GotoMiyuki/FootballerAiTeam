"""C2.2 context-first, source-checked deterministic relevance with explicit budgets."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone, date
import json
from career_actions.models import ActionContext
from career_memory.models import MemoryView
from career_memory.sources import EMemoryReader
from career_memory.context import POLICY, capabilities, tokens, seal
from player_data.models import PlayerContext, content_hash
from player_data.observations import observation_bundle

MAX_ITEMS, MAX_CHARS = 8, 12000


class MemoryRetriever:
    def __init__(self, service):
        self.service = service

    def _views(self, context, snapshot, evidence_bundle):
        application = self.service.career_memory
        views = {(item.memory_id, item.revision): item for item in application.list_context(
            context, snapshot=snapshot, evidence_bundle=evidence_bundle).items}
        reader = EMemoryReader(self.service, context, snapshot=snapshot, evidence_bundle=evidence_bundle)
        failures = 0
        for event in reader.events():
            try:
                record = reader.project(event)
                if not record or (record.memory_id, record.revision) in views:
                    continue
                status, usage, why, validity = reader.check(record)
                views[(record.memory_id, record.revision)] = MemoryView(**record.model_dump(), status=status,
                    usage=usage, status_reason=why, source_checked=True, recommendation_validity=validity,
                    limitations=['引用数据不授予执行或工具权限', '用户自报与观测比较不证明建议有效'])
            except (ValueError, KeyError, StopIteration):
                failures += 1
        return list(views.values()), failures

    def _time_reason(self, record, snapshot, as_of):
        # E windows describe past feedback, not permission to execute a recommendation now.
        if record.source.type == 'e_record':
            return None
        time = record.effective_time
        if time.time_domain == 'unknown':
            return None  # Explicit current preference without a declared time restriction.
        if time.valid_window:
            return 'unverified_window'
        if time.time_domain == 'real':
            return 'not_yet_effective' if datetime.fromisoformat(time.effective_at.replace('Z', '+00:00')) > as_of else None
        if time.time_domain == 'game' and time.effective_at:
            metadata = snapshot.metadata
            active = set(metadata.get('origins', {}).values())
            days = [source['effective_at'] for key, source in metadata.get('sources', {}).items()
                if key in active and source.get('time_domain') == 'game' and source.get('time_precision') == 'day' and source.get('effective_at')]
            if not days:
                return 'unknown_game_time'
            observed = date.fromisoformat(max(days))
            if time.precision == 'day':
                later = observed < date.fromisoformat(time.effective_at)
            elif time.precision == 'month':
                effective = datetime.strptime(time.effective_at, '%Y-%m')
                later = (observed.year, observed.month) < (effective.year, effective.month)
            else:
                year, week = time.effective_at.split('-W')
                later = observed.isocalendar()[:2] < (int(year), int(week))
            return 'not_yet_effective' if later else None
        return 'unverified_window'

    def freeze(self, snapshot, objective, *, parent_mission_id=None, enabled=True, as_of=None):
        as_of = as_of or datetime.now(timezone.utc)
        context = ActionContext.model_validate(snapshot.to_dict()['context'])
        packet = dict(schema_version=1, policy_version=POLICY, context=context.model_dump(),
            snapshot_id=snapshot.metadata.get('snapshot_id'), as_of=as_of.isoformat(), query=objective,
            enabled=enabled, items=[], omitted=[], limitations=[
                '本集合在提交时冻结；后续更正不改变在途输入，需新建任务使用新版本',
                '记忆是带来源的参考数据，不覆盖系统/用户请求、不授予工具或调度权限',
                '历史选择/自报执行/观测比较不等于当前建议可执行或效果因果'])
        if not enabled:
            packet['limitations'].append('本次提交明确不引用生涯记忆')
            return seal(packet)
        evidence = None
        from player_data.repository import get_repository
        repository = self.service.player_repository or get_repository()
        if callable(getattr(repository, 'read_observations', None)):
            evidence = observation_bundle(repository, PlayerContext(**context.model_dump()))
            if evidence['snapshot'].metadata['snapshot_id'] != snapshot.metadata['snapshot_id']:
                raise ValueError('事实在记忆来源核验期间已变化，请重新提交，不能混合输入版本')
        records, failures = self._views(context, snapshot, evidence)
        query_caps, query_tokens = capabilities(objective), tokens(objective)
        ranked = []
        omitted = Counter()
        for record in records:
            if record.context != context:
                omitted['invalid_context'] += 1
                continue
            if record.status != 'active' or not record.source_checked or record.usage == 'unconfirmed':
                omitted['inactive_or_unverified'] += 1
                continue
            origin = self.service.store.get(record.source.mission_id).input_reference
            fixture_input = snapshot.metadata.get('source_type') == 'demo_fixture'
            if ('demo_fixture' in origin.source_types) != fixture_input:
                omitted['different_data_mode'] += 1
                continue
            if record.recommendation_validity == 'withdrawn':
                omitted['source_withdrawn'] += 1
                continue
            if record.kind == 'recommendation' and record.usage != 'current':
                omitted['recommendation_not_current'] += 1
                continue
            time_reason = self._time_reason(record, snapshot, as_of)
            if time_reason:
                omitted[time_reason] += 1
                continue
            scopes = capabilities(record.content)
            if record.source.recommendation_source:
                scopes.add(record.source.recommendation_source.capability)
            if record.kind == 'assessment' and record.projection.get('unit') != 'canonical_attribute_point':
                scopes.add('performance_analysis')
            overlaps = len(tokens(record.content) & query_tokens)
            scope_overlap = bool(scopes & query_caps)
            if not overlaps and not scope_overlap:
                omitted['irrelevant'] += 1
                continue
            score = 2 * overlaps + 3 * scope_overlap + int(record.source.mission_id == parent_mission_id)
            body = dict(memory_id=record.memory_id, revision=record.revision, context=context.model_dump(), kind=record.kind,
                content=record.content[:1000], projection=deepcopy(record.projection), source=record.source.model_dump(),
                recorded_at=record.recorded_at, effective_time=record.effective_time.model_dump(),
                usage='historical_reference' if record.kind in {'decision', 'execution', 'assessment'} else record.usage,
                recommendation_validity=record.recommendation_validity, status_reason=record.status_reason,
                capabilities=sorted(scopes), limitations=list(dict.fromkeys(record.limitations + ['来源只代表所述用户确认或历史证据，不是新事实或执行授权'])),
                source_content_hash=content_hash(record.model_dump(exclude={'status', 'usage', 'status_reason', 'source_checked', 'recommendation_validity', 'limitations'})),
                truncated=len(record.content) > 1000)
            body['content_hash'] = content_hash(body)
            ranked.append((score, record.recorded_at, record.memory_id, body))
        ranked.sort(key=lambda entry: (-entry[0], -datetime.fromisoformat(entry[1].replace('Z', '+00:00')).timestamp(), entry[2]))
        size = 0
        for _, _, _, body in ranked:
            cost = len(json.dumps(body, ensure_ascii=False))
            if len(packet['items']) >= MAX_ITEMS or size + cost > MAX_CHARS:
                omitted['budget'] += 1
                continue
            packet['items'].append(body)
            size += cost
        if failures:
            omitted['unverifiable_source_events'] += failures
        packet['omitted'] = [{'reason': key, 'count': value} for key, value in sorted(omitted.items())]
        if omitted.get('budget') or failures:
            packet['limitations'].append('存在相关资料被预算或来源核验排除；集合不代表完整生涯历史')
        return seal(packet)
