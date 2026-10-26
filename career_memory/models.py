from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from career_actions.models import ActionContext, RecommendationSource


class MemoryTime(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    time_domain: Literal['unknown', 'real', 'game', 'match'] = 'unknown'
    precision: Literal['unknown', 'unspecified', 'instant', 'day', 'month', 'week'] = 'unknown'
    effective_at: str | None = Field(default=None, min_length=1, max_length=80)
    valid_window: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode='after')
    def time_is_explicit(self):
        if self.time_domain == 'unknown':
            if self.precision != 'unknown' or self.effective_at or self.valid_window:
                raise ValueError('未知时间域不能填写生效时间、精度或窗口')
        elif self.effective_at is None:
            if self.precision != 'unspecified' or not self.valid_window:
                raise ValueError('未指定生效时点时必须保留原窗口描述，精度为unspecified')
        elif self.time_domain == 'real' and self.precision == 'instant':
            if datetime.fromisoformat(self.effective_at.replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('现实生效时刻必须包含时区')
        elif self.time_domain == 'game':
            if self.precision == 'day':
                date.fromisoformat(self.effective_at)
            elif self.precision == 'month':
                datetime.strptime(self.effective_at, '%Y-%m')
            elif self.precision == 'week':
                year, week = self.effective_at.split('-W')
                date.fromisocalendar(int(year), int(week), 1)
            else:
                raise ValueError('游戏生效时间需要day/month/week精度')
        else:
            raise ValueError('不支持的时间域或生效精度')
        return self


class MemorySource(BaseModel):
    type: Literal['user_confirmed', 'model_candidate', 'e_record']
    mission_id: str
    request_id: str | None = None
    candidate_id: str | None = None
    confirmed_candidate_revision: int | None = None
    event_id: str | None = None
    event_type: str | None = None
    object_id: str | None = None
    object_revision: int | None = None
    object_hash: str | None = None
    recommendation_id: str | None = None
    recommendation_revision: int | None = None
    recommendation_source: RecommendationSource | None = None


class MemoryRecord(BaseModel):
    schema_version: Literal[1] = 1
    memory_id: str
    revision: int
    context: ActionContext
    kind: Literal['goal', 'preference', 'recommendation', 'decision', 'execution', 'assessment']
    content: str
    projection: dict = Field(default_factory=dict)
    source: MemorySource
    recorded_at: str
    effective_time: MemoryTime = Field(default_factory=MemoryTime)
    corrects_revision: int | None = None
    supersedes_memory_id: str | None = None
    change_reason: str = ''


class MemoryView(MemoryRecord):
    status: Literal['active', 'candidate', 'superseded', 'retracted']
    usage: Literal['current', 'historical_only', 'unconfirmed']
    status_reason: str
    source_checked: bool
    recommendation_validity: Literal['current', 'needs_reassessment', 'superseded', 'withdrawn'] | None = None
    limitations: list[str] = Field(default_factory=list)


class MemoryList(BaseModel):
    context: ActionContext
    items: list[MemoryView] = Field(default_factory=list)


class MemoryWriteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    context: ActionContext
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    operation: Literal['create', 'correct', 'retract', 'confirm_candidate']
    confirmed: Literal[True]
    kind: Literal['goal', 'preference']
    content: str = Field(default='', max_length=2000)
    effective_time: MemoryTime = Field(default_factory=MemoryTime)
    memory_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    reason: str = Field(default='', max_length=1000)

    @model_validator(mode='after')
    def operation_is_explicit(self):
        if self.operation == 'create':
            if self.memory_id is not None or self.expected_revision is not None:
                raise ValueError('新记忆不关联已有版本')
        elif not self.memory_id or not self.expected_revision:
            raise ValueError('变更需要原记忆id和expected_revision')
        if self.operation in {'create', 'correct', 'confirm_candidate'} and not self.content:
            raise ValueError('明确确认的目标或偏好不能为空')
        if self.operation in {'correct', 'retract'} and not self.reason:
            raise ValueError('更正或撤回需要原因')
        return self


class CandidateProposal(BaseModel):
    """Internal candidate only. Never registered as an HTTP/LLM write tool."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    context: ActionContext
    candidate_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    kind: Literal['goal', 'preference']
    content: str = Field(min_length=1, max_length=2000)


class MemoryAccepted(BaseModel):
    record: MemoryRecord
    created: bool


class MemorySyncResult(BaseModel):
    context: ActionContext
    consumed_events: int
    created_memories: int
