"""Lightweight runtime telemetry for the LoopController.

The controller keeps telemetry in graph state so a checkpoint contains the
numbers needed to evaluate loop efficiency.  It intentionally records only
operational metadata (counts, duration and provider token usage), never prompt
or response bodies.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List


COUNTER_FIELDS = (
    "llm_call_count",
    "manager_call_count",
    "reviewer_call_count",
    "agent_call_count",
    "review_count",
    "revision_count",
    "replan_count",
)


def default_telemetry() -> Dict[str, Any]:
    """Return the complete, serialisable telemetry shape for a new mission."""
    return {
        **{field: 0 for field in COUNTER_FIELDS},
        "latency_ms": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "events": [],
    }


def token_usage_from_response(response: Any) -> Dict[str, int]:
    """Read common LangChain/provider usage fields without coupling to one LLM."""
    usage = getattr(response, "usage_metadata", None) or {}
    metadata = getattr(response, "response_metadata", None) or {}
    if not usage and isinstance(metadata, dict):
        usage = metadata.get("token_usage") or metadata.get("usage") or {}
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0}

    def as_int(*keys: str) -> int:
        for key in keys:
            try:
                value = usage.get(key)
                if value is not None:
                    return max(0, int(value))
            except (TypeError, ValueError):
                pass
        return 0

    return {
        "input_tokens": as_int("input_tokens", "prompt_tokens"),
        "output_tokens": as_int("output_tokens", "completion_tokens"),
    }


def merge_telemetry(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """Sum state deltas while retaining a bounded, audit-friendly event trail."""
    if isinstance(new, dict) and new.get("__RESET_TELEMETRY__"):
        existing = {}
    merged = default_telemetry()
    for source in (existing or {}, new or {}):
        if not isinstance(source, dict):
            continue
        for field in COUNTER_FIELDS:
            try:
                merged[field] += max(0, int(source.get(field, 0) or 0))
            except (TypeError, ValueError):
                continue
        for field in ("latency_ms",):
            try:
                merged[field] += max(0.0, float(source.get(field, 0) or 0))
            except (TypeError, ValueError):
                continue
        for field in ("input_tokens", "output_tokens"):
            try:
                merged[field] += max(0, int(source.get(field, 0) or 0))
            except (TypeError, ValueError):
                continue
        merged["events"].extend(
            item for item in source.get("events", []) or [] if isinstance(item, dict)
        )
    # An endless event log would defeat the cost-control purpose of telemetry.
    merged["events"] = merged["events"][-200:]
    return merged


def make_node_telemetry_delta(
    *,
    node_name: str,
    role: str,
    latency_ms: float,
    llm_events: Iterable[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """Convert one graph-node execution into a mergeable telemetry delta."""
    events: List[Dict[str, Any]] = [
        {"kind": "node", "node": node_name, "role": role, "latency_ms": round(latency_ms, 3)}
    ]
    input_tokens = output_tokens = 0
    llm_count = 0
    for event in llm_events or ():
        if not isinstance(event, dict):
            continue
        llm_count += 1
        try:
            input_tokens += max(0, int(event.get("input_tokens", 0) or 0))
            output_tokens += max(0, int(event.get("output_tokens", 0) or 0))
        except (TypeError, ValueError):
            pass
        events.append(dict(event))

    delta = default_telemetry()
    delta.update({
        "llm_call_count": llm_count,
        "latency_ms": round(max(0.0, latency_ms), 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "events": events,
    })
    if role == "manager":
        delta["manager_call_count"] = 1
    elif role == "reviewer":
        delta["reviewer_call_count"] = 1
        delta["review_count"] = 1
    elif role == "agent":
        delta["agent_call_count"] = 1
    if node_name == "manager_revision":
        delta["revision_count"] = 1
    elif node_name == "manager_replan":
        delta["replan_count"] = 1
    return delta
