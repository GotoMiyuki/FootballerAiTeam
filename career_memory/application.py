"""C2.1 explicit user memory plus on-demand E reference projection. No model calls."""
from uuid import uuid4
from backend.events import now
from career_actions.models import ActionContext
from career_memory.models import (MemoryRecord, MemorySource, MemoryList, MemoryView,
                                  MemoryWriteRequest, CandidateProposal, MemorySyncResult)
from career_memory.repository import CareerMemoryRepository
from career_memory.sources import EMemoryReader
from player_data.models import content_hash


class CareerMemoryApplication:
    def __init__(self, service):
        self.service = service
        self.repository = CareerMemoryRepository(service.store)

    def context(self, mission_id, requested=None):
        mission = self.service.store.get(mission_id)
        reference = mission.input_reference
        if not reference.context or reference.verification not in {'VERIFIED', 'DEMO'}:
            raise ValueError('来源任务缺少可核验context，不能建立生涯记忆')
        context = ActionContext.model_validate(reference.context)
        if requested is not None and context != requested:
            raise ValueError('记忆操作不能切换生涯、分支或球员')
        return context

    def list(self, mission_id):
        return self.list_context(self.context(mission_id))

    def list_context(self, context, *, snapshot=None, evidence_bundle=None):
        with self.service.store.lock:
            reader = EMemoryReader(self.service, context, snapshot=snapshot, evidence_bundle=evidence_bundle)
            items = []
            for record, saved_status in self.repository.records(context):
                status, usage = saved_status, 'current'
                reason, checked, validity = '用户明确确认的目标/偏好；不属于A事实', True, None
                limitations = ['仅是有来源的记忆引用，不自动写入A事实或授予操作权限']
                if record.source.type == 'e_record':
                    limitations += ['E拥有行动权威状态；本记录为可追溯引用', '用户自报执行不等于游戏核验或效果证明']
                    try:
                        status, usage, reason, validity = reader.check(record)
                    except (ValueError, KeyError, StopIteration):
                        status, usage, checked = 'retracted', 'historical_only', False
                        reason = 'E来源缺失或无法核验，不能作为当前记忆；原内容保留'
                    if saved_status == 'superseded':
                        status, usage = 'superseded', 'historical_only'
                elif saved_status == 'candidate':
                    usage, reason = 'unconfirmed', '模型候选尚未经用户确认，不是当前目标或偏好'
                elif saved_status != 'active':
                    usage, reason = 'historical_only', '目标/偏好已更正或撤回，原版本保留'
                items.append(MemoryView(**record.model_dump(), status=status, usage=usage,
                    status_reason=reason, source_checked=checked, recommendation_validity=validity, limitations=limitations))
            return MemoryList(context=context, items=items)

    def sync(self, mission_id):
        with self.service.store.lock:
            context = self.context(mission_id)
            reader = EMemoryReader(self.service, context)
            events = reader.events()
            # Resolve every source before beginning the write transaction. No caller-supplied event bodies.
            projections = [(event, reader.project(event)) for event in events]
            consumed, created = 0, 0
            with self.service.store.db:
                self.service.store.db.execute('BEGIN IMMEDIATE')
                for event, record in projections:
                    consumed += self.repository.consume_event(event)
                    if record:
                        suffix = f':v{record.revision}' if record.kind == 'assessment' else ''
                        created += self.repository.append(record, 'active', f'e:{record.kind}:{record.source.object_id}{suffix}')
            return MemorySyncResult(context=context, consumed_events=consumed, created_memories=created)

    def write(self, mission_id, request: MemoryWriteRequest):
        fingerprint = content_hash([mission_id, request.model_dump()])
        with self.service.store.lock, self.service.store.db:
            context = self.context(mission_id, request.context)
            self.service.store.db.execute('BEGIN IMMEDIATE')
            duplicate = self.repository.request_result(request.request_id, fingerprint)
            if duplicate:
                return duplicate, False
            previous, previous_status = None, None
            if request.memory_id:
                previous, previous_status = self.repository.latest(request.memory_id, context)
                if previous.source.type == 'e_record':
                    raise ValueError('E建议/选择/执行记忆只能从权威来源同步，不能在C中改写或撤回')
                if previous.revision != request.expected_revision or previous_status in {'superseded', 'retracted'}:
                    raise ValueError('记忆版本已变化或已撤回，请刷新后重试')
                if previous.kind != request.kind:
                    raise ValueError('更正不能改变目标/偏好类别')
                if request.operation == 'confirm_candidate' and previous_status != 'candidate':
                    raise ValueError('只能明确确认尚未确认的模型候选')
                if previous_status == 'candidate' and request.operation not in {'confirm_candidate', 'retract'}:
                    raise ValueError('模型候选必须通过明确确认入口才能成为当前偏好')
            source = MemorySource(type='user_confirmed', mission_id=mission_id, request_id=request.request_id,
                candidate_id=previous.source.candidate_id if previous else None,
                confirmed_candidate_revision=previous.revision if request.operation == 'confirm_candidate' else None)
            record = MemoryRecord(memory_id=previous.memory_id if previous else f'mem_{uuid4().hex}',
                revision=previous.revision + 1 if previous else 1, context=context, kind=request.kind,
                content=previous.content if request.operation == 'retract' else request.content,
                source=source, recorded_at=now(),
                effective_time=previous.effective_time if request.operation == 'retract' else request.effective_time,
                corrects_revision=previous.revision if previous else None, change_reason=request.reason)
            self.repository.append(record, 'retracted' if request.operation == 'retract' else 'active')
            self.service.store.db.execute('INSERT INTO career_memory_requests VALUES (?,?,?,?)',
                (request.request_id, fingerprint, record.memory_id, record.revision))
        return record, True

    def propose_candidate(self, mission_id, proposal: CandidateProposal):
        """Internal staging API only; this does not extract from chat or confirm preferences."""
        with self.service.store.lock, self.service.store.db:
            context = self.context(mission_id, proposal.context)
            self.service.store.db.execute('BEGIN IMMEDIATE')
            key = f'candidate:{mission_id}:{proposal.candidate_id}'
            existing = self.service.store.db.execute('SELECT payload FROM career_memories WHERE source_key=?', (key,)).fetchone()
            if existing:
                original = MemoryRecord.model_validate_json(existing[0])
                if original.content != proposal.content or original.kind != proposal.kind or original.context != context:
                    raise ValueError('同一模型候选id不能覆盖原内容')
                return original, False
            record = MemoryRecord(memory_id='mem_' + content_hash(key), revision=1, context=context,
                kind=proposal.kind, content=proposal.content, recorded_at=now(),
                source=MemorySource(type='model_candidate', mission_id=mission_id, candidate_id=proposal.candidate_id))
            self.repository.append(record, 'candidate', key)
        return record, True
