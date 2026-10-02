from typing import Literal
from pydantic import BaseModel, Field
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


class RecommendationList(BaseModel):
    mission_id: str
    availability: Literal['AVAILABLE', 'UNAVAILABLE', 'NOT_PROJECTED']
    reason: str
    items: list[RecommendationView] = Field(default_factory=list)


class RecommendationEvent(BaseModel):
    event_id: str
    sequence: int
    schema_version: Literal[1] = 1
    type: Literal['recommendation.created', 'recommendation.superseded', 'recommendation.withdrawn']
    recommendation_id: str
    revision: int
    context: PlayerContextRequest
    source: RecommendationSource
    timestamp: str
