"""Public V0.1 DTOs: no LangGraph state or model reasoning crosses this boundary."""
from typing import Any, Literal
from pydantic import BaseModel, Field

MissionStatus = Literal['CREATED', 'PLANNING', 'RUNNING', 'REVIEWING', 'REVISING', 'REPLANNING', 'BLOCKED', 'COMPLETED', 'FAILED']
SubtaskStatus = Literal['PENDING', 'RUNNING', 'COMPLETED', 'REVISION_REQUIRED', 'BLOCKED', 'SKIPPED']
EventType = Literal['mission.created', 'mission.started', 'mission.status_changed', 'mission.blocked', 'mission.completed', 'mission.failed', 'plan.created', 'plan.updated', 'subtask.started', 'subtask.completed', 'subtask.revision_required', 'agent.started', 'agent.completed', 'review.started', 'review.completed', 'revision.started', 'revision.completed', 'replan.started', 'replan.completed', 'result.created', 'report.created']

class SubtaskView(BaseModel):
    id: str
    title: str
    status: SubtaskStatus = 'PENDING'
    assigned_agent: str = 'Manager'
    revision_count: int = 0

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
    decision: Literal['PASS', 'REVISE', 'REPLAN', 'BLOCKED']
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
    intent: Literal['new_mission', 'followup'] = 'new_mission'
    demo_scenario: Literal['pass', 'revision', 'blocked', 'replan'] = 'pass'

class MessageView(BaseModel):
    id: str
    role: Literal['user', 'assistant', 'system']
    content: str
    created_at: str
    mission_id: str

class MessageAccepted(BaseModel):
    message_id: str
    mission_id: str

class InputRequest(BaseModel):
    values: dict[str, str | float | bool]
