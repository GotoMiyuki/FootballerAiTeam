from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.models import PlayerInputReference
from career_actions.models import ActionContext, RecommendationSource

METRICS = {
    'speed': {'label': '速度', 'path': 'profile/attributes/physical/speed', 'kind': 'profile_patch', 'method': 'value_delta', 'unit': 'canonical_attribute_point', 'scale': '0..100'},
    'finishing': {'label': '射门终结', 'path': 'profile/attributes/offense/finishing', 'kind': 'profile_patch', 'method': 'value_delta', 'unit': 'canonical_attribute_point', 'scale': '0..100'},
    'rating': {'label': '比赛评分', 'path': 'rating', 'kind': 'match_record', 'method': 'mean', 'unit': 'canonical_rating_point', 'scale': '0..10'},
    'goals': {'label': '个人进球', 'path': 'goals', 'kind': 'match_record', 'method': 'mean', 'unit': 'goals_per_match', 'scale': 'nonnegative_integer'},
    'assists': {'label': '个人助攻', 'path': 'assists', 'kind': 'match_record', 'method': 'mean', 'unit': 'assists_per_match', 'scale': 'nonnegative_integer'},
    'minutes_played': {'label': '出场分钟', 'path': 'minutes_played', 'kind': 'match_record', 'method': 'mean', 'unit': 'minutes_per_match', 'scale': 'nonnegative'},
}


class ComparisonWindow(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['next_matches', 'game_dates']
    count: int | None = Field(default=None, ge=1, le=20, strict=True)
    start: str | None = None
    end: str | None = None

    @model_validator(mode='after')
    def window_is_explicit(self):
        if self.kind == 'next_matches':
            if self.count is None or self.start is not None or self.end is not None:
                raise ValueError('后续比赛窗口只填写count')
        else:
            if self.count is not None or not self.start or not self.end:
                raise ValueError('日期窗口必须填写游戏开始和结束日期')
            if date.fromisoformat(self.start).isoformat() != self.start or date.fromisoformat(self.end).isoformat() != self.end or self.start > self.end:
                raise ValueError('游戏日期必须为有效YYYY-MM-DD且开始不晚于结束')
        return self


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context: ActionContext
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    recommendation_revision: int = Field(ge=1, strict=True)
    confirmed: Literal[True]
    metric: Literal['speed', 'finishing', 'rating', 'goals', 'assists', 'minutes_played']
    baseline_count: int = Field(default=1, ge=1, le=20, strict=True)
    minimum_samples: int = Field(default=1, ge=1, le=20, strict=True)
    window: ComparisonWindow
    target_value: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    target_operator: Literal['gte', 'lte'] = 'gte'

    @model_validator(mode='after')
    def meaningful_spec(self):
        attribute = METRICS[self.metric]['kind'] == 'profile_patch'
        if attribute and (self.window.kind != 'game_dates' or self.baseline_count != 1 or self.minimum_samples != 1):
            raise ValueError('能力点比较使用日期窗口和单次能力快照')
        if self.minimum_samples > self.baseline_count or self.window.count and self.minimum_samples > self.window.count:
            raise ValueError('最低样本数不能超过窗口或基线场数')
        if self.target_value is not None and ((attribute and self.target_value > 100) or (self.metric == 'rating' and self.target_value > 10)):
            raise ValueError('目标超过所选规范量表')
        return self


class ComparisonPlan(BaseModel):
    schema_version: Literal[1] = 1
    plan_id: str
    recommendation_id: str
    recommendation_revision: int
    context: ActionContext
    source: RecommendationSource
    baseline_reference: PlayerInputReference
    anchor_at: str
    specification: ComparisonRequest
    metric_definition: dict
    baseline_observations: list[dict]
    created_at: str
    algorithm_version: Literal['observed-comparison-v1'] = 'observed-comparison-v1'


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    context: ActionContext
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    expected_revision: int = Field(ge=0, strict=True)
    execution_id: str | None = Field(default=None, min_length=1, max_length=100)


class AssessmentResult(BaseModel):
    evidence_status: Literal['COMPLETE', 'INSUFFICIENT', 'INCOMPARABLE']
    goal_status: Literal['MET', 'NOT_MET', 'UNDETERMINED']
    before: float | None
    after: float | None
    delta: float | None
    baseline_samples: int
    followup_samples: int
    baseline_missing: int
    followup_missing: int
    window_matches: int
    window_complete: bool
    baseline_observations: list[dict]
    followup_observations: list[dict]
    missing: list[str]
    exclusions: dict[str, int]
    unit: str
    scale: str
    method: str
    time_domain: Literal['game']
    time_precision: Literal['day']
    execution: dict
    limitations: list[str]
    reason: str


class Assessment(BaseModel):
    schema_version: Literal[1] = 1
    assessment_id: str
    revision: int
    plan_id: str
    recommendation_id: str
    context: ActionContext
    source: RecommendationSource
    created_at: str
    corrects_revision: int | None = None
    signature: str
    baseline_reference: PlayerInputReference
    observed_snapshot_reference: dict
    algorithm_version: Literal['observed-comparison-v1'] = 'observed-comparison-v1'
    execution_requested_id: str | None = None
    result: AssessmentResult


class AssessmentView(Assessment):
    validity: Literal['current', 'superseded', 'unavailable']
    validity_reason: str


class ComparisonView(BaseModel):
    context: ActionContext
    supported: bool
    reason: str
    metrics: dict = Field(default_factory=lambda: METRICS)
    plans: list[ComparisonPlan] = Field(default_factory=list)
    assessments: list[AssessmentView] = Field(default_factory=list)


class PlanAccepted(BaseModel):
    record: ComparisonPlan
    created: bool


class AssessmentAccepted(BaseModel):
    record: Assessment
    created: bool
