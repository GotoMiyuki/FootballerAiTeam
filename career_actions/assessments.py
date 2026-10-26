"""E2.1 immutable specifications and revisioned deterministic assessments."""
from uuid import uuid4
from backend.events import now
from career_actions.assessment_models import (METRICS, ComparisonPlan, Assessment, AssessmentView, ComparisonView)
from career_actions.comparison import anchor, freeze_baseline, calculate, LIMITATIONS
from player_data.models import PlayerContext, content_hash
from player_data.observations import observation_bundle


class AssessmentApplication:
    def __init__(self, service):
        self.service = service
        with service.store.lock, service.store.db:
            service.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS comparison_plans (id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS assessments (id TEXT NOT NULL, revision INTEGER NOT NULL,
                    plan_id TEXT NOT NULL, signature TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS assessment_requests (request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    @property
    def store(self):
        return self.service.store

    def recommendation(self, mission_id, recommendation_id, context=None, *, snapshot=None):
        mission = self.store.get(mission_id)
        if not mission.input_reference.context:
            raise ValueError('任务没有可核验球员context')
        actual = PlayerContext(**mission.input_reference.context)
        if context is not None and context.model_dump() != actual.to_dict():
            raise ValueError('比较不能切换生涯、分支或球员')
        return self.service.recommendations._action_recommendation(mission_id, recommendation_id, actual, snapshot=snapshot)

    def _replay(self, request_id, fingerprint, kind):
        row = self.store.db.execute('SELECT request_hash,kind,payload FROM assessment_requests WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            return None
        if row[0] != fingerprint or row[1] != kind:
            raise ValueError('同一request_id不能用于不同的比较规范或评估请求')
        return (ComparisonPlan if kind == 'plan' else Assessment).model_validate_json(row[2])

    def create_plan(self, mission_id, recommendation_id, request):
        fingerprint = content_hash([mission_id, recommendation_id, request.model_dump()])
        with self.store.lock, self.store.db:
            rec = self.recommendation(mission_id, recommendation_id, request.context)
            duplicate = self._replay(request.request_id, fingerprint, 'plan')
            if duplicate:
                return duplicate, False
            if rec.revision != request.recommendation_revision or rec.validity != 'current':
                raise ValueError('比较规范必须在建议版本当前有效、尚无新事实时确认；请重新评估后建立新规范')
            bundle = observation_bundle(self.service.player_repository, PlayerContext(**request.context.model_dump()))
            metadata = bundle['snapshot'].metadata
            if metadata['snapshot_id'] != rec.applicability.input_reference.snapshot_id or metadata['state_version'] != rec.applicability.input_reference.state_version:
                raise ValueError('基线已变化，不能在观察结果出现后挑选基线或目标')
            anchor_at = anchor(bundle)
            if request.window.kind == 'game_dates' and request.window.start <= anchor_at:
                raise ValueError('后续窗口必须在冻结基线最新有效游戏日期之后')
            plan = ComparisonPlan(plan_id=f'cmp_{uuid4().hex}', recommendation_id=rec.recommendation_id,
                recommendation_revision=rec.revision, context=request.context, source=rec.source,
                baseline_reference=rec.applicability.input_reference, anchor_at=anchor_at, specification=request,
                metric_definition=METRICS[request.metric], baseline_observations=freeze_baseline(bundle, request, anchor_at), created_at=now())
            self.store.db.execute('BEGIN IMMEDIATE')
            self.store.db.execute('INSERT INTO comparison_plans VALUES (?,?,?)', (plan.plan_id, recommendation_id, plan.model_dump_json()))
            self.store.db.execute('INSERT INTO assessment_requests VALUES (?,?,?,?)', (request.request_id, fingerprint, 'plan', plan.model_dump_json()))
        return plan, True

    def plan(self, plan_id, recommendation_id):
        row = self.store.db.execute('SELECT recommendation_id,payload FROM comparison_plans WHERE id=?', (plan_id,)).fetchone()
        if not row:
            raise KeyError(plan_id)
        if row[0] != recommendation_id:
            raise ValueError('比较规范不属于请求建议')
        return ComparisonPlan.model_validate_json(row[1])

    def records(self, plan_id):
        rows = self.store.db.execute('SELECT payload,status FROM assessments WHERE plan_id=? ORDER BY revision', (plan_id,)).fetchall()
        return [(Assessment.model_validate_json(row[0]), row[1]) for row in rows]

    def execution(self, rec, identity):
        result = {'requested_id': identity, 'execution_id': None, 'status': 'UNKNOWN', 'source_type': None, 'revision': None}
        if identity is None:
            return result
        activity = self.service.recommendations.repository.activity(rec)
        by_id = {record.execution_id: record for record in activity.executions}
        if identity not in by_id:
            raise ValueError('执行记录不属于该建议/context')
        action = by_id[identity]
        seen = set()
        while action.execution_id not in activity.current_execution_ids:
            if action.execution_id in seen:
                raise ValueError('执行更正关系不明确')
            seen.add(action.execution_id)
            successors = [row for row in activity.executions if row.corrects_execution_id == action.execution_id]
            if len(successors) != 1:
                raise ValueError('执行记录当前版本无法核验')
            action = successors[0]
        if action.context.model_dump() != rec.context.model_dump() or action.source != rec.source or action.recommendation_revision != rec.revision:
            raise ValueError('执行记录来源或建议版本不一致')
        return dict(requested_id=identity, execution_id=action.execution_id, revision=action.revision,
            status=action.status, source_type=action.source_type, content_hash=content_hash(action.model_dump()))

    def compute(self, plan, rec, execution_id, *, evidence_bundle=None):
        execution = self.execution(rec, execution_id)
        snapshot = {}
        try:
            if plan.metric_definition != METRICS[plan.specification.metric]:
                raise ValueError('比较指标规范版本不一致，不能沿用原量表计算')
            if rec.source != plan.source or rec.revision != plan.recommendation_revision or rec.validity == 'withdrawn':
                raise ValueError('原建议来源不可核验；只能保留历史评估')
            bundle = evidence_bundle if evidence_bundle is not None else observation_bundle(self.service.player_repository, PlayerContext(**plan.context.model_dump()))
            snapshot = {key: bundle['snapshot'].metadata[key] for key in ('state_version', 'snapshot_id')}
            snapshot['context'] = plan.context.model_dump()
            result = calculate(plan, bundle, execution)
        except ValueError as error:
            result = dict(evidence_status='INCOMPARABLE', goal_status='UNDETERMINED', before=None, after=None,
                delta=None, baseline_samples=0, followup_samples=0, baseline_missing=0, followup_missing=0,
                baseline_observations=[], followup_observations=[], missing=[], exclusions={},
                window_matches=0, window_complete=False, execution=execution, limitations=list(LIMITATIONS),
                reason=str(error), unit=plan.metric_definition['unit'], scale=plan.metric_definition['scale'], method=plan.metric_definition['method'],
                time_domain='game', time_precision='day')
        signature = content_hash([plan.model_dump(), {key: value for key, value in result.items() if key != 'exclusions'}])
        return result, snapshot, signature

    def evaluate(self, mission_id, recommendation_id, plan_id, request):
        fingerprint = content_hash([mission_id, recommendation_id, plan_id, request.model_dump()])
        with self.store.lock, self.store.db:
            rec = self.recommendation(mission_id, recommendation_id, request.context)
            duplicate = self._replay(request.request_id, fingerprint, 'assessment')
            if duplicate:
                return duplicate, False
            plan = self.plan(plan_id, recommendation_id)
            records = self.records(plan_id)
            previous = records[-1][0] if records else None
            if request.expected_revision != (previous.revision if previous else 0):
                raise ValueError('评估版本已变化，请刷新后提交')
            result, snapshot, signature = self.compute(plan, rec, request.execution_id)
            self.store.db.execute('BEGIN IMMEDIATE')
            if previous and previous.signature == signature:
                record, created = previous, False
            else:
                record = Assessment(assessment_id='asm_' + content_hash(plan_id), revision=previous.revision + 1 if previous else 1,
                    plan_id=plan_id, recommendation_id=recommendation_id, context=request.context, source=rec.source,
                    created_at=now(), corrects_revision=previous.revision if previous else None, signature=signature,
                    baseline_reference=plan.baseline_reference, observed_snapshot_reference=snapshot,
                    execution_requested_id=request.execution_id, result=result)
                if previous:
                    self.store.db.execute("UPDATE assessments SET status='superseded' WHERE id=?", (previous.assessment_id,))
                    self.service.recommendations.repository._event(rec, 'assessment.superseded',
                        object_id=previous.assessment_id, object_revision=previous.revision, previous_record_id=None)
                self.store.db.execute('INSERT INTO assessments VALUES (?,?,?,?,?,?)',
                    (record.assessment_id, record.revision, plan_id, signature, record.model_dump_json(), 'current'))
                self.service.recommendations.repository._event(rec, 'assessment.created', object_id=record.assessment_id,
                    object_revision=record.revision, previous_record_id=f'{record.assessment_id}:v{previous.revision}' if previous else None)
                created = True
            self.store.db.execute('INSERT INTO assessment_requests VALUES (?,?,?,?)',
                (request.request_id, fingerprint, 'assessment', record.model_dump_json()))
        return record, created

    def view(self, mission_id, recommendation_id, *, snapshot=None, evidence_bundle=None):
        with self.store.lock:
            rec = self.recommendation(mission_id, recommendation_id, snapshot=snapshot)
            supported, reason = callable(getattr(self.service.player_repository, 'read_observations', None)), '规范在新事实出现前确认；评估只报告观测变化'
            if not supported:
                reason = '当前样本Repository没有有来源观察，不能生成实际比较'
            plans = [ComparisonPlan.model_validate_json(row[0]) for row in self.store.db.execute(
                'SELECT payload FROM comparison_plans WHERE recommendation_id=? ORDER BY rowid', (recommendation_id,)).fetchall()]
            views = []
            for plan in plans:
                for record, saved in self.records(plan.plan_id):
                    validity, why = saved, '原比较规范和观察引用仍一致；不代表建议有效'
                    if saved == 'current':
                        try:
                            _, _, signature = self.compute(plan, rec, record.execution_requested_id, evidence_bundle=evidence_bundle)
                            if signature != record.signature:
                                validity, why = 'superseded', '后续观察、原观察更正或执行反馈已变化；原数值保留，需生成新版本'
                        except ValueError:
                            validity, why = 'unavailable', '来源或执行引用无法核验，原评估仅供历史查看'
                    views.append(AssessmentView(**record.model_dump(), validity=validity, validity_reason=why))
            return ComparisonView(context=rec.context.model_dump(), supported=supported, reason=reason, plans=plans, assessments=views)
