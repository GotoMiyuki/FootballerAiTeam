"""Read-only E adapter. All source identities come from E, never sync request payloads."""
from career_memory.models import MemoryRecord, MemorySource, MemoryTime
from player_data.models import content_hash


def reference_id(kind, object_id):
    return 'mem_' + content_hash(['e-memory-v1', kind, object_id])


class EMemoryReader:
    def __init__(self, service, context, *, snapshot=None, evidence_bundle=None):
        self.service, self.context = service, context
        self.repository = service.recommendations.repository
        self.missions, self.activities = {}, {}
        self.snapshot, self.evidence_bundle = snapshot, evidence_bundle
        self.assessments = {}

    def events(self):
        events = []
        for mission in self.service.store.missions():
            if mission.input_reference.context != self.context.model_dump():
                continue
            after = 0
            while True:
                batch = self.repository.events(mission.id, after)
                if not batch:
                    break
                if any(event.source.mission_id != mission.id for event in batch):
                    raise ValueError('E事件来源任务与事件存储位置不一致')
                events.extend(batch)
                after = batch[-1].sequence
        return sorted(events, key=lambda event: event.sequence)

    def resolve(self, event):
        if event.context.model_dump() != self.context.model_dump():
            raise ValueError('E事件context与记忆同步上下文不一致')
        mission_id = event.source.mission_id
        mission = self.service.store.get(mission_id)
        if mission.input_reference.context != self.context.model_dump():
            raise ValueError('E来源任务context不一致')
        if mission_id not in self.missions:
            originals = {record.recommendation_id: record for record, _ in self.repository.records(mission_id)}
            views = {record.recommendation_id: record for record in self.service.recommendations.list(mission_id, snapshot=self.snapshot).items}
            self.missions[mission_id] = originals, views
        originals, views = self.missions[mission_id]
        rec, view = originals.get(event.recommendation_id), views.get(event.recommendation_id)
        if (not rec or not view or rec.context != event.context or rec.source != event.source
                or rec.revision != event.revision):
            raise ValueError('E事件建议版本、成果来源或身份无法核验')
        if event.type.startswith('recommendation.'):
            if event.object_id is not None or event.previous_record_id is not None:
                raise ValueError('建议生命周期事件不能冒充行动对象')
            return rec, view, None, None
        if event.type.startswith('assessment.'):
            if rec.recommendation_id not in self.assessments:
                self.assessments[rec.recommendation_id] = self.service.assessments.view(mission_id,
                    rec.recommendation_id, snapshot=self.snapshot, evidence_bundle=self.evidence_bundle).assessments
            assessment = next((item for item in self.assessments[rec.recommendation_id]
                if item.assessment_id == event.object_id and item.revision == event.object_revision), None)
            if not assessment or assessment.context.model_dump() != rec.context.model_dump() or assessment.source != rec.source:
                raise ValueError('评估事件与原对象/版本/context不一致')
            if event.type == 'assessment.created':
                expected = f'{assessment.assessment_id}:v{assessment.corrects_revision}' if assessment.corrects_revision else None
                if event.previous_record_id != expected:
                    raise ValueError('评估事件更正关系不一致')
            return rec, view, assessment, None
        if rec.recommendation_id not in self.activities:
            self.activities[rec.recommendation_id] = self.repository.activity(rec)
        activity = self.activities[rec.recommendation_id]
        kind = 'decision' if event.type == 'decision.recorded' else 'execution'
        records = activity.decisions if kind == 'decision' else activity.executions
        identity = 'decision_id' if kind == 'decision' else 'execution_id'
        action = next((record for record in records if getattr(record, identity) == event.object_id), None)
        if (not action or action.context.model_dump() != rec.context.model_dump() or action.source != rec.source
                or action.recommendation_id != rec.recommendation_id
                or action.recommendation_revision != rec.revision or action.revision != event.object_revision):
            raise ValueError('E事件行动对象、版本或来源不一致')
        previous = action.previous_decision_id if kind == 'decision' else action.corrects_execution_id
        prior = next((record for record in records if getattr(record, identity) == previous), None)
        if (previous != event.previous_record_id or (previous and not prior)
                or action.revision != (prior.revision + 1 if prior else 1)):
            raise ValueError('E行动的前次或更正关系无法核验')
        if kind == 'execution' and event.type != ('execution.corrected' if previous else 'execution.recorded'):
            raise ValueError('E执行事件类型与更正关系不符')
        return rec, view, action, activity

    def project(self, event):
        rec, view, action, activity = self.resolve(event)
        if event.type in {'recommendation.withdrawn', 'recommendation.superseded', 'assessment.superseded'}:
            return None
        if event.type == 'assessment.created':
            raw = action.model_dump(exclude={'validity', 'validity_reason'})
            result = raw['result']
            return MemoryRecord(memory_id=reference_id('assessment', action.assessment_id), revision=action.revision,
                context=rec.context.model_dump(), kind='assessment', content=f"观测比较：{result['evidence_status']} / {result['goal_status']}",
                projection={key: result[key] for key in ('evidence_status', 'goal_status', 'before', 'after', 'delta',
                    'baseline_samples', 'followup_samples', 'unit', 'scale', 'reason', 'limitations', 'execution')},
                source=MemorySource(type='e_record', mission_id=rec.source.mission_id, event_id=event.event_id,
                    event_type=event.type, object_id=action.assessment_id, object_revision=action.revision,
                    object_hash=content_hash(raw), recommendation_id=rec.recommendation_id,
                    recommendation_revision=rec.revision, recommendation_source=rec.source), recorded_at=event.timestamp,
                corrects_revision=action.corrects_revision)
        kind = 'recommendation' if action is None else ('decision' if event.type == 'decision.recorded' else 'execution')
        identity = rec.recommendation_id if action is None else event.object_id
        source = MemorySource(type='e_record', mission_id=rec.source.mission_id,
            event_id=event.event_id, event_type=event.type, object_id=identity,
            object_revision=rec.revision if action is None else action.revision,
            object_hash=content_hash((action or rec).model_dump()), recommendation_id=rec.recommendation_id,
            recommendation_revision=rec.revision, recommendation_source=rec.source)
        time, previous = MemoryTime(), None
        if kind == 'recommendation':
            content = rec.content.text[:1200]
            projection = {'truncated': len(rec.content.text) > 1200,
                'execution_support': rec.execution_support.status,
                'baseline_reference': rec.applicability.input_reference.model_dump()}
        elif kind == 'decision':
            content = f'玩家选择：{action.status}'
            projection = {'status': action.status, 'reason': action.reason[:1200], 'truncated': len(action.reason) > 1200}
            previous = action.previous_decision_id
        else:
            content = action.actual_action[:1200]
            projection = {'status': action.status, 'source_type': action.source_type,
                'truncated': len(action.actual_action) > 1200 or len(action.correction_reason) > 1200,
                'correction_reason': action.correction_reason[:1200]}
            window = action.window
            time = MemoryTime(time_domain=window.time_domain,
                precision='unknown' if window.time_domain == 'unknown' else 'unspecified', valid_window=window.label)
            previous = action.corrects_execution_id
        return MemoryRecord(memory_id=reference_id(kind, identity), revision=1, context=rec.context.model_dump(),
            kind=kind, content=content, projection=projection, source=source, recorded_at=event.timestamp,
            effective_time=time, supersedes_memory_id=reference_id(kind, previous) if previous else None)

    def check(self, memory):
        event = self.repository.event(memory.source.mission_id, memory.source.event_id)
        if event is None or self.project(event) != memory:
            raise ValueError('E来源事件缺失或记忆与权威记录投影不一致')
        rec, view, action, activity = self.resolve(event)
        if memory.kind == 'assessment':
            if action.validity != 'current' or view.validity == 'withdrawn':
                return 'superseded', 'historical_only', '比较来源或观察已变化，不能作为当前评估', view.validity
            return 'active', 'current', '当前比较版本已核验；观测变化不证明建议效果', view.validity
        if action is not None:
            current = activity.current_decision_id == action.decision_id if memory.kind == 'decision' else action.execution_id in activity.current_execution_ids
            if not current:
                return 'superseded', 'historical_only', 'E原选择或执行反馈已被后续记录更正/替代，历史保留', view.validity
        if view.validity == 'withdrawn':
            return 'retracted', 'historical_only', '建议来源不可用，仅保留历史引用；不否认过去的用户反馈', view.validity
        if view.validity == 'superseded':
            return 'superseded', 'historical_only', '原建议成果已被替代，仅保留历史引用', view.validity
        if view.validity == 'needs_reassessment':
            return 'active', 'historical_only', '原建议当前适用性待重评；历史行动不能冒充当前适用建议', view.validity
        return 'active', 'current', 'E事件、原记录与当前来源已核验；执行仍仅为用户自报', view.validity
