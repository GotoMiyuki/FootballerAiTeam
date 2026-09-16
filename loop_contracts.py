"""V2 loop data contracts and compatibility migration helpers.

These types deliberately coexist with the legacy graph fields.  They provide a
stable Subtask-level representation before the Loop Controller starts routing
on V2 decisions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict


SubtaskStatus = Literal[
    "PENDING", "RUNNING", "COMPLETED", "REVISION_REQUIRED", "BLOCKED", "SKIPPED",
]
HypothesisStatus = Literal["OPEN", "SUPPORTED", "WEAKENED", "REJECTED"]
ReviewDecision = Literal["PASS", "REVISE", "REPLAN", "BLOCKED"]
FindingAction = Literal["KEEP", "REVISION", "REPLAN", "BLOCKED"]
Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
LoopMode = Literal[
    "PLANNING", "REVIEW", "REVISION", "REPLANNING", "BLOCKED", "FINISHED",
]
ManagerDecision = Literal["KEEP", "REVISION", "REPLAN", "BLOCKED", "FINISH"]


MAX_REVISIONS_PER_SUBTASK = 1
MAX_REPLANS_PER_MISSION = 2
MAX_TOTAL_LOOPS = 4


class Subtask(TypedDict, total=False):
    id: str
    objective: str
    capability: str
    assigned_agent: str
    dependencies: List[str]
    status: SubtaskStatus
    revision_count: int
    last_result_version: int
    observation: Dict[str, Any]
    result: Dict[str, Any]
    blocked_reason: str
    revision_context: Dict[str, Any]


class SubtaskResult(TypedDict, total=False):
    subtask_id: str
    status: str
    observation: Dict[str, Any]
    recommendation: str
    evidence: List[Any]
    assumptions: List[str]
    uncertainties: List[str]
    constraints_checked: List[str]
    source_version: int
    blocked_reason: str


class ReviewFinding(TypedDict, total=False):
    id: str
    subtask_ids: List[str]
    severity: Severity
    category: str
    description: str
    evidence: List[Any]
    action: FindingAction
    reason: str


class ReviewResult(TypedDict, total=False):
    decision: ReviewDecision
    findings: List[ReviewFinding]
    reviewed_subtasks: List[str]
    blocking_information: List[str]
    summary: str


class Hypothesis(TypedDict, total=False):
    id: str
    statement: str
    confidence: float
    status: HypothesisStatus
    supporting_evidence: List[Any]
    contradicting_evidence: List[Any]
    created_in_plan_version: int
    updated_in_plan_version: int


class Plan(TypedDict, total=False):
    version: int
    objective: str
    hypothesis_ids: List[str]
    constraints: List[str]
    information_gaps: List[str]
    subtasks: List[Subtask]
    dependencies: Dict[str, List[str]]
    revision_targets: List[str]
    termination_reason: str


class LoopBudget(TypedDict, total=False):
    max_revisions: int
    max_replans: int
    max_total_iterations: int


class LoopControl(TypedDict, total=False):
    mode: LoopMode
    budget: LoopBudget
    total_iterations: int
    replan_count: int
    revision_targets: List[str]
    last_decision: ManagerDecision
    waiting_for_user: bool
    warnings: List[str]
    termination_reason: str


_SUBTASK_STATUS_MAP = {
    "pending": "PENDING",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "needs_revision": "REVISION_REQUIRED",
    "revision_required": "REVISION_REQUIRED",
    "failed": "REVISION_REQUIRED",
    "blocked": "BLOCKED",
    "skipped": "SKIPPED",
}
_HYPOTHESIS_STATUS_MAP = {
    "open": "OPEN",
    "supported": "SUPPORTED",
    "weakened": "WEAKENED",
    "rejected": "REJECTED",
}
_REVIEW_STATUS_MAP = {
    "passed": "PASS",
    "needs_revision": "REVISE",
    # Legacy "failed" mixed internal Reviewer failure with safety failure. It
    # must not become missing-user-input BLOCKED without an explicit finding.
    "failed": "PASS",
}
_FINDING_ACTION_MAP = {
    "keep": "KEEP",
    "revision": "REVISION",
    "replan": "REPLAN",
    "blocked": "BLOCKED",
    "accept_with_uncertainty": "KEEP",
    "revise_subtask": "REVISION",
    "recalculate": "REVISION",
    "resolve_conflict": "REVISION",
    "request_more_evidence": "BLOCKED",
}
_SEVERITY_MAP = {
    "low": "LOW", "medium": "MEDIUM", "high": "HIGH", "critical": "CRITICAL",
    "info": "INFO",
}

_REVIEW_DECISIONS = {"PASS", "REVISE", "REPLAN", "BLOCKED"}
_FINDING_ACTIONS = {"KEEP", "REVISION", "REPLAN", "BLOCKED"}
_SEVERITIES = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _list_value(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def default_loop_control() -> LoopControl:
    """Return conservative, non-telemetry loop limits from the V2 guide."""
    return {
        "mode": "PLANNING",
        "budget": {
            "max_revisions": MAX_REVISIONS_PER_SUBTASK,
            "max_replans": MAX_REPLANS_PER_MISSION,
            "max_total_iterations": MAX_TOTAL_LOOPS,
        },
        "total_iterations": 0,
        "replan_count": 0,
        "revision_targets": [],
        "last_decision": "KEEP",
        "waiting_for_user": False,
        "warnings": [],
        "termination_reason": "",
    }


def normalise_loop_control(value: Any) -> LoopControl:
    """Validate persisted LoopControl data while retaining unknown-safe defaults."""
    defaults = default_loop_control()
    raw = value if isinstance(value, dict) else {}
    raw_budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else {}

    def positive_int(candidate: Any, fallback: int) -> int:
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0 else fallback

    def non_negative_int(candidate: Any) -> int:
        try:
            return max(0, int(candidate or 0))
        except (TypeError, ValueError):
            return 0

    budget: LoopBudget = {
        "max_revisions": positive_int(
            raw_budget.get("max_revisions"), MAX_REVISIONS_PER_SUBTASK,
        ),
        "max_replans": positive_int(
            raw_budget.get("max_replans"), MAX_REPLANS_PER_MISSION,
        ),
        "max_total_iterations": positive_int(
            raw_budget.get("max_total_iterations"), MAX_TOTAL_LOOPS,
        ),
    }
    mode = str(raw.get("mode", defaults["mode"])).upper()
    if mode not in {"PLANNING", "REVIEW", "REVISION", "REPLANNING", "BLOCKED", "FINISHED"}:
        mode = defaults["mode"]
    last_decision = str(raw.get("last_decision", defaults["last_decision"])).upper()
    if last_decision not in {"KEEP", "REVISION", "REPLAN", "BLOCKED", "FINISH"}:
        last_decision = defaults["last_decision"]
    return {
        "mode": mode,
        "budget": budget,
        "total_iterations": non_negative_int(raw.get("total_iterations")),
        "replan_count": non_negative_int(raw.get("replan_count")),
        "revision_targets": [str(item) for item in _list_value(raw.get("revision_targets"))],
        "last_decision": last_decision,
        "waiting_for_user": bool(raw.get("waiting_for_user", False)),
        "warnings": [str(item) for item in _list_value(raw.get("warnings"))],
        "termination_reason": str(raw.get("termination_reason", "")),
    }


def migrate_subtask(legacy: Dict[str, Any]) -> Subtask:
    """Convert the existing lower-case Plan task shape to V2 without mutation."""
    status = _SUBTASK_STATUS_MAP.get(str(legacy.get("status", "pending")).lower(), "PENDING")
    migrated: Subtask = {
        "id": str(legacy.get("id", "")),
        "objective": str(legacy.get("objective") or legacy.get("goal") or ""),
        "capability": str(legacy.get("capability", "")),
        "assigned_agent": str(legacy.get("assigned_agent", "")),
        "dependencies": list(legacy.get("dependencies") or legacy.get("depends_on") or []),
        "status": status,
        "revision_count": int(legacy.get("revision_count", 0) or 0),
        "last_result_version": int(legacy.get("last_result_version", 0) or 0),
        "blocked_reason": str(legacy.get("blocked_reason", "")),
    }
    if isinstance(legacy.get("observation"), dict):
        migrated["observation"] = dict(legacy["observation"])
    if isinstance(legacy.get("result"), dict):
        migrated["result"] = dict(legacy["result"])
    if isinstance(legacy.get("revision_context"), dict):
        migrated["revision_context"] = dict(legacy["revision_context"])
    return migrated


def migrate_hypothesis(legacy: Dict[str, Any], plan_version: int) -> Hypothesis:
    """Convert lower-case legacy hypothesis statuses into the V2 contract."""
    try:
        confidence = max(0.0, min(1.0, float(legacy.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "id": str(legacy.get("id", "")),
        "statement": str(legacy.get("statement", "")),
        "confidence": confidence,
        "status": _HYPOTHESIS_STATUS_MAP.get(str(legacy.get("status", "open")).lower(), "OPEN"),
        "supporting_evidence": list(legacy.get("supporting_evidence") or []),
        "contradicting_evidence": list(legacy.get("contradicting_evidence") or []),
        "created_in_plan_version": int(legacy.get("created_in_plan_version", plan_version) or plan_version),
        "updated_in_plan_version": int(legacy.get("updated_in_plan_version", plan_version) or plan_version),
    }


def normalise_review_result(raw_review: Any, plan_subtasks: List[Subtask]) -> ReviewResult:
    """Validate either a native V2 review or the legacy Reviewer payload.

    The normalised finding actions, rather than a legacy pass/fail flag, decide
    whether another loop is useful.  INFO/LOW findings are deliberately KEEP so
    stylistic preferences cannot create an infinite revision cycle.
    """
    review = raw_review if isinstance(raw_review, dict) else {}
    known_ids = {task.get("id") for task in plan_subtasks}
    known_ids.discard(None)
    raw_findings = review.get("findings") or []
    if not isinstance(raw_findings, list):
        raw_findings = [raw_findings]
    declared_decision = str(review.get("decision", "")).upper()
    legacy_status = str(review.get("status", "")).lower()
    findings: List[ReviewFinding] = []
    for index, finding in enumerate(raw_findings, 1):
        if not isinstance(finding, dict):
            finding = {"description": str(finding)}
        ids = finding.get("subtask_ids") or ([finding["subtask_id"]] if finding.get("subtask_id") else [])
        if not isinstance(ids, list):
            ids = [ids]
        ids = list(dict.fromkeys(str(task_id) for task_id in ids if str(task_id) in known_ids))
        severity = str(finding.get("severity", "MEDIUM")).upper()
        if severity not in _SEVERITIES:
            severity = _SEVERITY_MAP.get(severity.lower(), "MEDIUM")
        raw_action = str(finding.get("action", "")).strip()
        action = raw_action.upper() if raw_action.upper() in _FINDING_ACTIONS else _FINDING_ACTION_MAP.get(raw_action.lower())
        if not action:
            # Old needs_revision payloads omitted actions; native V2 payloads
            # must opt into a loop explicitly.
            action = "REVISION" if legacy_status == "needs_revision" and not declared_decision else "KEEP"
        if severity in {"INFO", "LOW"}:
            action = "KEEP"
        elif severity == "MEDIUM" and action == "REPLAN":
            # A medium, local issue cannot justify rebuilding hypotheses/Plan.
            action = "REVISION"
        if action in {"REVISION", "REPLAN", "BLOCKED"} and not ids:
            # A loop without an explicit affected scope is exactly the
            # amplification failure this controller is meant to prevent.
            action = "KEEP"
        findings.append({
            "id": str(finding.get("id") or f"finding_{index}"),
            "subtask_ids": ids,
            "severity": severity,
            "category": str(finding.get("category") or finding.get("type") or "review_issue"),
            "description": str(finding.get("description", "")),
            "evidence": _list_value(finding.get("evidence")),
            "action": action,
            "reason": str(finding.get("reason") or finding.get("description", "")),
        })

    blocking_information = [str(item) for item in _list_value(review.get("blocking_information"))]
    for finding in findings:
        if finding.get("action") != "BLOCKED":
            continue
        reason = str(finding.get("reason") or finding.get("description") or "").strip()
        if reason and reason not in blocking_information:
            blocking_information.append(reason)
    actions = {finding.get("action") for finding in findings}
    # Missing critical input takes precedence over speculative replanning: the
    # system must wait rather than fabricate data or retry an Agent.
    declared_blocker_is_substantive = (
        declared_decision == "BLOCKED"
        and bool(blocking_information)
        and not findings
    )
    if "BLOCKED" in actions or declared_blocker_is_substantive:
        decision = "BLOCKED"
    elif "REPLAN" in actions:
        decision = "REPLAN"
    elif "REVISION" in actions:
        decision = "REVISE"
    else:
        decision = "PASS"

    if not findings and declared_decision in _REVIEW_DECISIONS:
        decision = declared_decision
    elif not findings and legacy_status:
        decision = _REVIEW_STATUS_MAP.get(legacy_status, "PASS")

    reviewed_subtasks = [
        str(item) for item in _list_value(review.get("reviewed_subtasks"))
        if str(item) in known_ids
    ]
    if not reviewed_subtasks:
        reviewed_subtasks = [
            str(task["id"]) for task in plan_subtasks
            if task.get("status") == "COMPLETED" and task.get("id")
        ]
    return {
        "decision": decision,
        "findings": findings,
        "reviewed_subtasks": list(dict.fromkeys(reviewed_subtasks)),
        "blocking_information": blocking_information,
        "summary": str(review.get("summary") or review.get("reviewed_summary") or ""),
    }


def migrate_review(legacy_review: Dict[str, Any], plan_subtasks: List[Subtask]) -> ReviewResult:
    """Compatibility alias for persisted legacy review payloads."""
    return normalise_review_result(legacy_review, plan_subtasks)


def build_v2_state_patch(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build V2 fields from the existing state without changing legacy fields.

    Keeping this as a pure function makes migration safe to call from an entry
    node today and easy to remove once V2 becomes the only representation.
    """
    # Prefer the compatibility Plan while migration is in progress, but also
    # accept checkpoints which already contain only the V2 representation.
    legacy_plan = state.get("plan") or state.get("plan_v2") or {}
    try:
        plan_version = max(1, int(legacy_plan.get("version", state.get("plan_version", 1)) or 1))
    except (TypeError, ValueError):
        plan_version = 1
    subtasks = [migrate_subtask(task) for task in legacy_plan.get("subtasks", []) if isinstance(task, dict)]
    observations_by_id = {item.get("subtask_id"): item for item in state.get("observations", [])
                          if isinstance(item, dict) and item.get("subtask_id")}
    existing_results = dict(state.get("subtask_results") or {})
    for subtask in subtasks:
        subtask_id = subtask.get("id")
        observation = observations_by_id.get(subtask_id)
        if observation:
            subtask["observation"] = dict(observation)
        if subtask_id in existing_results:
            subtask["result"] = dict(existing_results[subtask_id])
    # An explicit [] means a known information gap was resolved.  Falling back
    # via ``or`` would silently resurrect it from Mission context.
    if "information_gaps" in legacy_plan:
        raw_information_gaps = legacy_plan.get("information_gaps")
    else:
        raw_information_gaps = (
            (state.get("mission") or {}).get("context", {}).get("information_gaps", [])
        )
    information_gaps = _list_value(raw_information_gaps)
    hypothesis_source = state.get("hypotheses") or state.get("hypotheses_v2") or []
    hypothesis_ids = [
        str(item.get("id", "")) for item in hypothesis_source
        if isinstance(item, dict) and item.get("id")
    ]
    if not hypothesis_ids:
        hypothesis_ids = [str(item) for item in _list_value(
            legacy_plan.get("hypothesis_ids") or legacy_plan.get("hypotheses")
        ) if str(item)]
    if "constraints" in legacy_plan:
        raw_constraints = legacy_plan.get("constraints")
    else:
        raw_constraints = (state.get("mission") or {}).get("constraints", [])
    plan_v2: Plan = {
        "version": plan_version,
        "objective": str(legacy_plan.get("objective") or (state.get("mission") or {}).get("objective", "")),
        "hypothesis_ids": hypothesis_ids,
        "constraints": _list_value(raw_constraints),
        "information_gaps": information_gaps,
        "subtasks": subtasks,
        "dependencies": {task["id"]: list(task.get("dependencies", [])) for task in subtasks if task.get("id")},
        "revision_targets": [],
        "termination_reason": str(legacy_plan.get("termination_reason") or state.get("termination_reason", "")),
    }
    review_v2: ReviewResult = {}
    native_review = state.get("review_v2")
    has_review = False
    if isinstance(native_review, dict) and native_review.get("decision"):
        has_review = True
        review_v2 = normalise_review_result(native_review, subtasks)
    elif state.get("review"):
        has_review = True
        legacy_review = dict(state.get("review") or {})
        legacy_review.setdefault("blocking_information", state.get("review_gaps") or [])
        review_v2 = migrate_review(legacy_review, subtasks)
    revision_targets = list(dict.fromkeys(
        subtask_id
        for finding in review_v2.get("findings", [])
        if finding.get("action") == "REVISION"
        for subtask_id in finding.get("subtask_ids", [])
    ))
    if not revision_targets and not has_review:
        revision_targets = list((state.get("plan_v2") or {}).get("revision_targets", []) or [])
    plan_v2["revision_targets"] = revision_targets
    loop_control = normalise_loop_control(state.get("loop_control"))
    loop_control["revision_targets"] = revision_targets
    return {
        "plan_v2": plan_v2,
        "subtasks": subtasks,
        "hypotheses_v2": [migrate_hypothesis(item, plan_version) for item in hypothesis_source if isinstance(item, dict)],
        "review_v2": review_v2,
        "subtask_results": existing_results,
        "loop_control": loop_control,
    }
