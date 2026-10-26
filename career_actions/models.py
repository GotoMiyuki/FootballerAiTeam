from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from backend.models import PlayerInputReference, PlayerContextRequest


class RecommendationSource(BaseModel):
    mission_id: str
    subtask_id: str
    capability: Literal['skill_training'] = 'skill_training'
    result_version: int
    payload_position: str
    payload_hash: str
    input_fingerprint: str
    review_plan_version: int
    reviewed_result_version: int
    mapping_version: Literal['training-focus-v1'] = 'training-focus-v1'


class RecommendationContent(BaseModel):
    title: str
    text: str
    expected_goal: str
    basis: str
    limitations: list[str]


class Applicability(BaseModel):
    input_reference: PlayerInputReference
    conditions: list[str]
    game_versions: list[str] = Field(default_factory=list)
    game_mode: str | None = None
    valid_window: str | None = None
    window_reason: str = '训练焦点未提供可核验的适用时间窗口'


class ExecutionSupport(BaseModel):
    status: Literal['pending_verification'] = 'pending_verification'
    reason: str = '尚无目标游戏/模式的操作证据，不能认定为可执行游戏行动'
    evidence_references: list[str] = Field(default_factory=list)


class EvaluationSpec(BaseModel):
    status: Literal['undefined'] = 'undefined'
    baseline_reference: PlayerInputReference
    metrics: list[str] = Field(default_factory=list)
    observation_window: str | None = None
    reason: str = '训练焦点未绑定有单位/量表的指标与观察窗口，不能计算效果或达标结论'


class Recommendation(BaseModel):
    schema_version: Literal[1] = 1
    recommendation_id: str
    revision: int = 1
    context: PlayerContextRequest
    source: RecommendationSource
    content: RecommendationContent
    applicability: Applicability
    execution_support: ExecutionSupport = Field(default_factory=ExecutionSupport)
    evaluation_spec: EvaluationSpec
    created_at: str


class RecommendationView(Recommendation):
    validity: Literal['current', 'needs_reassessment', 'superseded', 'withdrawn']
    validity_reason: str
    checked_state_version: str | None = None
    origin_feedback: dict | None = None


class RecommendationList(BaseModel):
    mission_id: str
    availability: Literal['AVAILABLE', 'UNAVAILABLE', 'NOT_PROJECTED']
    reason: str
    items: list[RecommendationView] = Field(default_factory=list)


class RecommendationEvent(BaseModel):
    event_id: str
    sequence: int
    schema_version: Literal[1] = 1
    type: Literal['recommendation.created', 'recommendation.superseded', 'recommendation.withdrawn',
                  'decision.recorded', 'execution.recorded', 'execution.corrected', 'assessment.created', 'assessment.superseded']
    recommendation_id: str
    revision: int
    context: PlayerContextRequest
    source: RecommendationSource
    timestamp: str
    object_id: str | None = None
    object_revision: int | None = None
    previous_record_id: str | None = None


class ActionContext(PlayerContextRequest):
    model_config = ConfigDict(extra='forbid')


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    recommendation_revision: int = Field(ge=1, strict=True)
    context: ActionContext


class DecisionRequest(ActionRequest):
    status: Literal['ADOPTED', 'REJECTED', 'DEFERRED']
    reason: str = Field(default='', max_length=2000)
    # Required CAS token: null explicitly means no preceding choice.
    previous_decision_id: str | None = Field(max_length=100)


class ExecutionWindow(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    time_domain: Literal['unknown', 'real', 'game', 'match'] = 'unknown'
    label: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode='after')
    def explicit_window(self):
        if (self.time_domain == 'unknown') != (self.label is None):
            raise ValueError('未知窗口不填范围；已知时间域必须填写实际时间或比赛范围')
        return self


class ExecutionRequest(ActionRequest):
    status: Literal['UNKNOWN', 'CONFIRMED', 'PARTIAL', 'NOT_EXECUTED']
    actual_action: str = Field(min_length=1, max_length=4000)
    window: ExecutionWindow = Field(default_factory=ExecutionWindow)
    corrects_execution_id: str | None = Field(default=None, min_length=1, max_length=100)
    correction_reason: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def correction_explained(self):
        if self.corrects_execution_id and not self.correction_reason:
            raise ValueError('更正执行反馈必须填写更正原因')
        if not self.corrects_execution_id and self.correction_reason:
            raise ValueError('更正原因需要关联原执行记录')
        return self


class ActionRecord(BaseModel):
    schema_version: Literal[1] = 1
    recommendation_id: str
    recommendation_revision: int
    context: ActionContext
    source: RecommendationSource
    request_id: str
    created_at: str
    # Applicability at submission is evidence, never a future validity guarantee.
    recommendation_validity: Literal['current', 'needs_reassessment', 'superseded', 'withdrawn']
    checked_state_version: str | None = None


class DecisionRecord(ActionRecord):
    decision_id: str
    revision: int
    status: Literal['ADOPTED', 'REJECTED', 'DEFERRED']
    reason: str
    previous_decision_id: str | None


class ExecutionRecord(ActionRecord):
    execution_id: str
    revision: int
    status: Literal['UNKNOWN', 'CONFIRMED', 'PARTIAL', 'NOT_EXECUTED']
    actual_action: str
    window: ExecutionWindow
    source_type: Literal['user_reported'] = 'user_reported'
    corrects_execution_id: str | None
    correction_reason: str


class RecommendationActivity(BaseModel):
    recommendation_id: str
    recommendation_revision: int
    context: ActionContext
    current_decision_id: str | None = None
    current_execution_ids: list[str] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    executions: list[ExecutionRecord] = Field(default_factory=list)


class DecisionAccepted(BaseModel):
    record: DecisionRecord
    created: bool


class ExecutionAccepted(BaseModel):
    record: ExecutionRecord
    created: bool
