"""Public V0.1 DTOs: no LangGraph state or model reasoning crosses this boundary."""
from typing import Any, Literal
from pydantic import BaseModel, Field, computed_field, ConfigDict

MissionStatus = Literal['CREATED', 'PLANNING', 'RUNNING', 'REVIEWING', 'REVISING', 'REPLANNING', 'BLOCKED', 'COMPLETED', 'FAILED']
SubtaskStatus = Literal['PENDING', 'RUNNING', 'COMPLETED', 'REVISION_REQUIRED', 'BLOCKED', 'SKIPPED', 'FAILED', 'INVALIDATED']
EventType = Literal['mission.created', 'mission.started', 'mission.status_changed', 'mission.blocked', 'mission.completed', 'mission.failed', 'plan.created', 'plan.updated', 'subtask.failed', 'subtask.invalidated', 'agent.failed', 'review.unavailable', 'review.invalidated', 'subtask.started', 'subtask.completed', 'subtask.revision_required', 'agent.started', 'agent.completed', 'review.started', 'review.completed', 'revision.started', 'revision.completed', 'replan.started', 'replan.completed', 'result.created', 'report.created', 'message.created']

class SubtaskView(BaseModel):
    id: str
    title: str
    status: SubtaskStatus = 'PENDING'
    assigned_agent: str = 'Manager'
    revision_count: int = 0
    result_summary: str | None = None
    result_version: int | None = None
    reason: str = ''

class PlanView(BaseModel):
    version: int = 1
    objective: str = ''
    reason: str = ''
    subtasks: list[SubtaskView] = Field(default_factory=list)

class AgentActivity(BaseModel):
    name: str
    status: Literal['PENDING', 'RUNNING', 'COMPLETED', 'FAILED'] = 'PENDING'
    activity: str = '等待任务'

class ReviewView(BaseModel):
    availability: Literal['NOT_RUN', 'COMPLETED', 'UNAVAILABLE'] = 'NOT_RUN'
    decision: Literal['PASS', 'REVISE', 'REPLAN', 'BLOCKED'] | None = None
    scope: str = 'specialist_inputs'
    reviewed_versions: dict[str, int] = Field(default_factory=dict)
    summary: str = ''
    affected_subtasks: list[str] = Field(default_factory=list)
    severity: str = 'INFO'

class RequiredInput(BaseModel):
    key: str
    label: str
    input_type: Literal['text', 'number', 'scale', 'boolean', 'single_select'] = 'text'
    required: bool = True
    min: float | None = None
    max: float | None = None
    options: list[str] = Field(default_factory=list)

class BlockedView(BaseModel):
    reason: str
    message: str
    required_inputs: list[RequiredInput]

class ReportSummary(BaseModel):
    title: str
    plan_version: int

class PlayerInputReference(BaseModel):
    verification: Literal['VERIFIED', 'DEMO', 'UNVERIFIED'] = 'UNVERIFIED'
    context: dict[str, str] | None = None
    state_version: str | None = None
    snapshot_id: str | None = None
    source_types: list[str] = Field(default_factory=list)

class HistoryReference(BaseModel):
    kind: Literal['report', 'result']
    mission_id: str
    subtask_id: str | None = None
    version: int
    content_hash: str

class MissionLineage(BaseModel):
    parent_mission_id: str
    operation: Literal['reevaluate', 'retry']
    reason: str
    request_id: str
    history_references: list[HistoryReference] = Field(default_factory=list)

class MissionView(BaseModel):
    id: str
    conversation_id: str
    title: str
    objective: str
    status: MissionStatus = 'CREATED'
    created_at: str
    sequence: int = 0
    plan: PlanView | None = None
    agents: list[AgentActivity] = Field(default_factory=list)
    review: ReviewView | None = None
    review_history: list[ReviewView] = Field(default_factory=list)
    blocked: BlockedView | None = None
    result: str | None = None
    report: ReportSummary | None = None
    error: str | None = None
    telemetry: dict[str, int | float] = Field(default_factory=dict)
    delivery_status: str = 'NOT_GENERATED'
    body_validation: dict[str, Any] = Field(default_factory=dict)
    input_reference: PlayerInputReference = Field(default_factory=PlayerInputReference)
    resume_error: str | None = None
    lineage: MissionLineage | None = None

    @computed_field
    @property
    def available_operations(self) -> list[str]:
        operations = ['view']
        if self.status == 'COMPLETED' and self.report and self.delivery_status == 'PUBLISHABLE':
            operations.append('explain')
            if self.input_reference.context and self.input_reference.verification != 'UNVERIFIED':
                operations.append('reevaluate')
        if self.status == 'FAILED' and self.input_reference.context and self.input_reference.verification != 'UNVERIFIED':
            operations.append('retry')
        if self.status == 'BLOCKED' and self.blocked and not self.resume_error:
            if self.input_reference.verification == 'UNVERIFIED':
                return operations
            if self.blocked.reason == 'missing_user_input':
                operations.append('supply_input')
            elif self.blocked.reason == 'report_approval':
                operations.append('approve_report')
        return operations

class MissionEvent(BaseModel):
    event_id: str
    type: EventType
    mission_id: str
    timestamp: str
    sequence: int
    data: dict[str, Any]

class MessageRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    content: str = Field(min_length=1, max_length=8000)
    mission_id: str | None = None
    intent: Literal['new_mission', 'followup', 'explain'] = 'new_mission'
    demo_scenario: Literal['pass', 'revision', 'blocked', 'replan'] = 'pass'

class MessageView(BaseModel):
    id: str
    role: Literal['user', 'assistant', 'system']
    content: str
    created_at: str
    mission_id: str
    kind: Literal['task', 'explanation', 'resume'] = 'task'
    operation_status: Literal['COMPLETED', 'FAILED'] = 'COMPLETED'
    in_reply_to: str | None = None

class MessageAccepted(BaseModel):
    message_id: str
    mission_id: str

class InputRequest(BaseModel):
    values: dict[str, str | float | bool]

class PlayerContextRequest(BaseModel):
    career_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    branch_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')
    player_id: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')

class ResultSelection(BaseModel):
    subtask_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)

class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parent_mission_id: str = Field(min_length=1, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    operation: Literal['reevaluate', 'retry'] = 'reevaluate'
    reason: str = Field(min_length=1, max_length=1000)
    content: str = Field(min_length=1, max_length=8000)
    player_context: PlayerContextRequest | None = None
    include_report: bool = True
    selected_results: list[ResultSelection] = Field(default_factory=list, max_length=8)
    recommendation_id: str | None = Field(default=None, min_length=1, max_length=100)
    assessment_id: str | None = Field(default=None, min_length=1, max_length=100)

class ContinuationAccepted(MessageAccepted):
    created: bool
