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
        "usage_tracking_version": 1,
        "known_input_tokens": 0, "known_output_tokens": 0,
        "usage_unknown_calls": 0,
        "events": [],
    }


def token_usage_from_response(response: Any) -> Dict[str, Any]:
    """Absent/invalid usage stays unknown; a provider's explicit zero is known."""
    usage = getattr(response, 'usage_metadata', None) or {}
    metadata = getattr(response, 'response_metadata', None) or {}
    if not usage and isinstance(metadata, dict):
        usage = metadata.get('token_usage') or metadata.get('usage') or {}
    def count(*keys):
        for key in keys:
            value = usage.get(key) if isinstance(usage, dict) else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
            if isinstance(value, str) and value.isdecimal():
                return int(value)
        return None
    return {'input_tokens': count('input_tokens', 'prompt_tokens'),
        'output_tokens': count('output_tokens', 'completion_tokens')}


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
        current = source.get('usage_tracking_version') == 1
        for token in ('input_tokens', 'output_tokens'):
            known = source.get('known_' + token, 0) if current else 0
            merged['known_' + token] += known or 0
        merged['usage_unknown_calls'] += (source.get('usage_unknown_calls', 0) if current
            else source.get('llm_call_count', 0)) or 0
        # Old aggregates have no coverage marker, so a zero cannot certify no usage.
        for token in ('input_tokens', 'output_tokens'):
            if not current and source.get('llm_call_count', 0):
                merged[token] = None
            elif merged[token] is not None:
                value = source.get(token, 0)
                merged[token] = merged[token] + value if value is not None else None
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
    llm_count = unknown = 0
    input_complete = output_complete = True
    for event in llm_events or ():
        if not isinstance(event, dict):
            continue
        llm_count += 1
        incoming, outgoing = event.get('input_tokens'), event.get('output_tokens')
        input_complete = input_complete and incoming is not None
        output_complete = output_complete and outgoing is not None
        unknown += incoming is None or outgoing is None
        input_tokens += incoming or 0
        output_tokens += outgoing or 0
        events.append(dict(event))

    delta = default_telemetry()
    delta.update({
        "llm_call_count": llm_count,
        "latency_ms": round(max(0.0, latency_ms), 3),
        "input_tokens": input_tokens if input_complete else None,
        "output_tokens": output_tokens if output_complete else None,
        "known_input_tokens": input_tokens, "known_output_tokens": output_tokens,
        "usage_unknown_calls": unknown,
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
