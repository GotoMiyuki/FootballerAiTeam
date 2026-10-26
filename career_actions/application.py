"""E1.1 deterministic training-focus mapping and read-time source/applicability checks."""
import json
from uuid import uuid4
from backend.events import now
from career_actions.models import (Recommendation, RecommendationList, RecommendationView,
                                  DecisionRequest, ExecutionRequest, DecisionRecord, ExecutionRecord)
from career_actions.repository import RecommendationRepository
from career_actions.sources import MissionResultReader, SourceUnavailable
from execution_context import relevant_constraints
from player_data.models import PlayerContext, PlayerSnapshot, content_hash


class RecommendationApplication:
    def __init__(self, service):
        self.service = service
        self.repository = RecommendationRepository(service.store)
        self.reader = MissionResultReader(service)

    def _records(self, source):
        mission, state, snapshot = source['mission'], source['state'], source['snapshot']
        records = []
        metadata = snapshot.metadata
        active_origins = set(metadata.get('origins', {}).values())
        game_versions = sorted({item['game_version'] for key, item in metadata.get('sources', {}).items()
            if key in active_origins and isinstance(item, dict) and isinstance(item.get('game_version'), str)})
        for item in source['tasks']:
            task, result, payload = item['task'], item['result'], item['payload']
            if len(payload['focus_areas']) > 32 or len(json.dumps(payload, ensure_ascii=False)) > 32000:
                raise SourceUnavailable('训练焦点来源超过支持的记录预算，未生成建议')
            notes = payload.get('notes') or []
            limitations = [str(value) for value in result.get('uncertainties') or []]
            limitations += [notes] if isinstance(notes, str) else [str(value) for value in notes]
            limitations += ['建议只表示模型专业成果，不证明用户采纳、实际执行或能力变化']
            for index, focus in enumerate(payload['focus_areas']):
                position = f'/focus_areas/{index}'
                key = [mission.id, task['id'], result['source_version'], position, 'training-focus-v1']
                records.append(Recommendation.model_validate({
                    'recommendation_id': 'rec_' + content_hash(key), 'created_at': now(),
                    'context': snapshot.to_dict()['context'],
                    'source': {'mission_id': mission.id, 'subtask_id': task['id'],
                        'result_version': result['source_version'], 'payload_position': position,
                        'payload_hash': content_hash(payload), 'input_fingerprint': result['input_fingerprint'],
                        'review_plan_version': state['plan']['version'],
                        'reviewed_result_version': state['review_v2']['reviewed_versions'][task['id']]},
                    'content': {'title': focus, 'text': focus,
                        'expected_goal': task.get('goal') or task.get('objective') or mission.objective,
                        'basis': '来源专业成果通过当前结果版本审查；内容按训练焦点字段确定性投影',
                        'limitations': list(dict.fromkeys(limitations))},
                    'applicability': {'input_reference': mission.input_reference.model_dump(),
                        'conditions': relevant_constraints(state, task), 'game_versions': game_versions},
                    'evaluation_spec': {'baseline_reference': mission.input_reference.model_dump()}}))
        return records

    def _project(self, source):
        self.repository.save_projection(source['mission'].id, self._records(source))

    def record_delivery(self, mission, state):
        self._project(self.reader.verify(mission, state, completing=True))

    def project_mission(self, mission_id):
        """Writer-side replay/backfill. GET consumers never call this method."""
        self._project(self.reader.read(mission_id))

    def list(self, mission_id, *, snapshot=None):
        self.service.store.get(mission_id)
        availability, reason = self.repository.batch(mission_id)
        rows = self.repository.records(mission_id)
        if not rows:
            return RecommendationList(mission_id=mission_id, availability=availability, reason=reason)
        try:
            source = self.reader.read(mission_id)
            expected_records = {record.recommendation_id: record for record in self._records(source)}
            source_error = None
        except SourceUnavailable as error:
            source, source_error = None, str(error)
            expected_records = {}
        latest, latest_error = None, None
        if source:
            try:
                context = PlayerContext(**source['snapshot'].to_dict()['context'])
                latest = snapshot if snapshot is not None else self.service._read_submission_snapshot(context)
                if not isinstance(latest, PlayerSnapshot) or latest.to_dict()['context'] != context.to_dict():
                    raise ValueError('最新快照身份不一致')
            except Exception:
                latest = None
                latest_error = '最新球员输入无法核验，当前适用性待重新评估'
        items = []
        by_id = {item['task']['id']: item for item in source['tasks']} if source else {}
        for record, saved_validity in rows:
            validity, why = saved_validity, '来源有效，当前球员快照与原基线一致；游戏操作仍待验证'
            item = by_id.get(record.source.subtask_id)
            expected_record = expected_records.get(record.recommendation_id)
            if validity != 'current':
                why = '该来源建议已被替代或撤回，历史内容保留'
            elif source_error:
                validity, why = 'withdrawn', source_error
            elif not item or item['result']['source_version'] != record.source.result_version:
                validity, why = 'superseded', '原专业成果版本已变化，不再作为当前建议'
            elif (content_hash(item['payload']) != record.source.payload_hash
                    or item['result']['input_fingerprint'] != record.source.input_fingerprint
                    or record.context.model_dump() != source['snapshot'].to_dict()['context']):
                validity, why = 'withdrawn', '原来源内容、指纹或身份不一致，不能作为当前建议'
            elif (not expected_record or record.source != expected_record.source
                    or record.revision != expected_record.revision
                    or record.content != expected_record.content
                    or record.applicability != expected_record.applicability
                    or record.execution_support != expected_record.execution_support
                    or record.evaluation_spec != expected_record.evaluation_spec):
                validity, why = 'withdrawn', '建议记录与原专业投影不一致，不能作为当前建议'
            elif latest_error:
                validity, why = 'needs_reassessment', latest_error
            elif (latest.metadata.get('state_version') != record.applicability.input_reference.state_version
                    or latest.metadata.get('snapshot_id') != record.applicability.input_reference.snapshot_id):
                validity, why = 'needs_reassessment', '球员数据已变化，原建议仅适用于原快照；需重新评估当前适用性'
            items.append(RecommendationView(**record.model_dump(), validity=validity,
                validity_reason=why, checked_state_version=latest.metadata.get('state_version') if latest else None,
                origin_feedback=self.service.store.get(mission_id).lineage.feedback_reference
                    if self.service.store.get(mission_id).lineage else None))
        return RecommendationList(mission_id=mission_id, availability='AVAILABLE' if source else 'UNAVAILABLE',
            reason=source_error or reason, items=items)

    def get(self, recommendation_id, context, *, snapshot=None):
        with self.service.store.lock:
            row = self.service.store.db.execute('SELECT mission_id,payload FROM recommendations WHERE id=?', (recommendation_id,)).fetchone()
        if not row:
            raise KeyError(recommendation_id)
        record = Recommendation.model_validate_json(row[1])
        if record.context.model_dump() != context.to_dict():
            raise ValueError('建议不属于请求的生涯、分支或球员')
        return next(item for item in self.list(row[0], snapshot=snapshot).items if item.recommendation_id == recommendation_id)

    def _action_recommendation(self, mission_id, recommendation_id, context, revision=None, *, snapshot=None):
        self.service.store.get(mission_id)
        record = self.get(recommendation_id, context, snapshot=snapshot)
        if record.source.mission_id != mission_id:
            raise ValueError('建议不属于请求的来源任务')
        if revision is not None and record.revision != revision:
            raise ValueError('建议修订版本不一致，请刷新后重试')
        return record

    def activity(self, mission_id, recommendation_id, context):
        with self.service.store.lock:
            record = self._action_recommendation(mission_id, recommendation_id, context)
            return self.repository.activity(record)

    def followups(self, mission_id, recommendation_id, context):
        """Read C-owned lineage instead of maintaining a second creation ledger in E."""
        self._action_recommendation(mission_id, recommendation_id, context)
        items = []
        for mission in self.service.store.missions():
            lineage = mission.lineage
            if (not lineage or lineage.parent_mission_id != mission_id or not lineage.feedback_reference
                    or lineage.feedback_reference.get('recommendation_id') != recommendation_id
                    or mission.input_reference.context != context.to_dict()):
                continue
            items.append(dict(mission_id=mission.id, status=mission.status, reason=lineage.reason,
                created_at=mission.created_at, feedback_reference=lineage.feedback_reference,
                input_reference=mission.input_reference.model_dump(),
                recommendation_ids=[record.recommendation_id for record, _ in self.repository.records(mission.id)]))
        return {'items': items}

    def record_decision(self, mission_id, recommendation_id, request: DecisionRequest):
        return self._record_action(mission_id, recommendation_id, request, 'decision')

    def record_execution(self, mission_id, recommendation_id, request: ExecutionRequest):
        return self._record_action(mission_id, recommendation_id, request, 'execution')

    def _record_action(self, mission_id, recommendation_id, request, kind):
        """Local single writer. Feedback is never an ObservationImporter input."""
        fingerprint = content_hash([mission_id, recommendation_id, kind, request.model_dump()])
        with self.service.store.lock:
            record = self._action_recommendation(mission_id, recommendation_id,
                PlayerContext(**request.context.model_dump()), request.recommendation_revision)
            duplicate = self.repository.action_result(request.request_id, fingerprint, kind)
            if duplicate:
                return duplicate, False
            if kind == 'decision' and request.status == 'ADOPTED' and record.validity != 'current':
                raise ValueError('当前建议不可采纳：' + record.validity_reason)
            activity = self.repository.activity(record)
            common = dict(recommendation_id=record.recommendation_id, recommendation_revision=record.revision,
                context=request.context, source=record.source, request_id=request.request_id, created_at=now(),
                recommendation_validity=record.validity, checked_state_version=record.checked_state_version)
            if kind == 'decision':
                previous = next((item for item in activity.decisions
                    if item.decision_id == request.previous_decision_id), None)
                action = DecisionRecord(**common, decision_id=f'dec_{uuid4().hex}',
                    revision=previous.revision + 1 if previous else 1,
                    status=request.status, reason=request.reason, previous_decision_id=request.previous_decision_id)
            else:
                previous = next((item for item in activity.executions
                    if item.execution_id == request.corrects_execution_id), None)
                action = ExecutionRecord(**common, execution_id=f'exec_{uuid4().hex}',
                    revision=previous.revision + 1 if previous else 1, status=request.status,
                    actual_action=request.actual_action, window=request.window,
                    corrects_execution_id=request.corrects_execution_id, correction_reason=request.correction_reason)
            return self.repository.save_action(record, action, kind, fingerprint)
