"""Guide §24 regression coverage for the V2 LoopController.

The tests use deterministic node doubles: they exercise graph routing and the
real Manager revision/replan code without making network calls to an LLM.
"""

import json
import unittest
import uuid

from langgraph.types import Command

from agents.manager import ManagerAgent
from graph import _ready_subtask, build_graph, create_initial_state, merge_observations
from loop_contracts import build_v2_state_patch, normalise_review_result


class FakeResponse:
    def __init__(self, content, input_tokens=0, output_tokens=0):
        self.content = content
        self.usage_metadata = {"input_tokens": input_tokens, "output_tokens": output_tokens}


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False), 11, 7)


def task(task_id, capability="skill_training", status="pending", priority=1):
    return {
        "id": task_id, "goal": f"{task_id} objective", "purpose": "produce reviewable evidence",
        "capability": capability, "priority": priority, "depends_on": [], "status": status,
    }


def completed_result(task_id, version=1):
    return {
        "subtask_id": task_id, "status": "COMPLETED",
        "observation": {"facts": [f"{task_id} fact"], "findings": [], "data_used": [], "result": "ok"},
        "recommendation": "ok", "evidence": [], "assumptions": [], "uncertainties": [],
        "constraints_checked": [], "source_version": version, "blocked_reason": "",
    }


def state_for(tasks, *, hypotheses=None, results=None):
    state = create_initial_state("loop controller regression")
    state["mission"] = {
        "primary_goal": "produce a safe recommendation", "objective": "produce a safe recommendation",
        "constraints": [], "context": {"information_gaps": []},
    }
    state["plan"] = {
        "version": 1, "objective": state["mission"]["objective"], "constraints": [],
        "information_gaps": [], "subtasks": tasks, "revision_targets": [], "termination_reason": "",
    }
    state["hypotheses"] = hypotheses or []
    state["subtask_results"] = results or {}
    state.update(build_v2_state_patch(state))
    return state


def complete_output(label):
    return {
        "status": "COMPLETED", "result": f"{label} result", "facts": [f"{label} fact"],
        "findings": [], "evidence": [], "assumptions": [], "uncertainties": [],
        "constraints_checked": [], "recommendation": f"{label} recommendation",
    }


def build_harness(reviewer, *, outputs=None, manager_llm=None, calls=None):
    """Build a complete graph with deterministic specialists and real loop actions."""
    calls = calls if calls is not None else []
    manager = ManagerAgent(manager_llm)

    def manager_node(_state):
        return {}

    def checkpoint_node(_state):
        return {}

    def revision_node(state):
        return manager.run_revision(state)

    def replan_node(state):
        return manager.run_replan(state)

    for fn in (manager_node, checkpoint_node, revision_node, replan_node):
        fn._telemetry_agent = manager

    outputs = outputs or {}

    def specialist(display_name):
        def node(state):
            active = state.get("current_subtask") or "none"
            calls.append((display_name, active))
            payload = outputs.get(active, complete_output(active))
            if callable(payload):
                payload = payload(state)
            return {"domain_outputs": {display_name: payload}}
        return node

    def document_node(_state):
        calls.append(("Document", "final"))
        return {"final_report": "final report"}

    return build_graph({
        "manager": manager_node, "intent_checkpoint": checkpoint_node,
        "manager_revision": revision_node, "manager_replan": replan_node,
        "reviewer": reviewer, "nutrition": specialist("Nutrition"), "coach": specialist("Coach"),
        "analyst": specialist("Analyst"), "career": specialist("Career"), "document": document_node,
    }), calls


def invoke(graph, state):
    config = {"configurable": {"thread_id": f"loop-test-{uuid.uuid4()}"}}
    graph.invoke(state, config)
    return graph.get_state(config).values, config


class LoopingPlanContractTests(unittest.TestCase):
    def test_dependency_priority_and_observation_merge(self):
        state = create_initial_state("test")
        state["plan"] = {"subtasks": [
            {"id": "evidence", "capability": "performance_analysis", "priority": 2,
             "depends_on": [], "status": "pending"},
            {"id": "action", "capability": "skill_training", "priority": 1,
             "depends_on": ["evidence"], "status": "pending"},
        ]}
        self.assertEqual(_ready_subtask(state)["id"], "evidence")
        state["plan"]["subtasks"][0]["status"] = "completed"
        self.assertEqual(_ready_subtask(state)["id"], "action")
        self.assertEqual(merge_observations(
            [{"subtask_id": "action", "result": "evidence"}],
            [{"subtask_id": "action", "confidence": 0.8}],
        ), [{"subtask_id": "action", "result": "evidence", "confidence": 0.8}])

    def test_a_normal_task_passes_directly_to_document_with_telemetry(self):
        def reviewer(_state):
            return {"review_v2": {"decision": "PASS", "findings": [], "summary": "sufficient"}}

        graph, calls = build_harness(reviewer)
        result, _ = invoke(graph, state_for([task("subtask_01")]))
        telemetry = result["telemetry"]
        self.assertEqual(result["final_report"], "final report")
        self.assertEqual(telemetry["review_count"], 1)
        self.assertEqual(telemetry["revision_count"], 0)
        self.assertEqual(telemetry["replan_count"], 0)
        self.assertGreaterEqual(telemetry["agent_call_count"], 2)
        self.assertIn(("Coach", "subtask_01"), calls)

    def test_b_local_error_revises_only_the_affected_subtask(self):
        review_calls = []

        def reviewer(_state):
            review_calls.append(1)
            if len(review_calls) == 1:
                return {"review_v2": {"decision": "REVISE", "findings": [{
                    "id": "local", "subtask_ids": ["subtask_03"], "severity": "HIGH",
                    "category": "validity", "description": "repair local evidence", "evidence": [], "action": "REVISION",
                }]}}
            return {"review_v2": {"decision": "PASS", "findings": []}}

        tasks = [task("subtask_01", "performance_analysis", "completed"),
                 task("subtask_02", "nutrition_plan", "completed"),
                 task("subtask_03", "skill_training", "completed")]
        results = {item["id"]: completed_result(item["id"]) for item in tasks}
        graph, calls = build_harness(reviewer)
        result, _ = invoke(graph, state_for(tasks, results=results))
        statuses = {item["id"]: item["status"] for item in result["plan"]["subtasks"]}
        self.assertEqual(statuses, {"subtask_01": "completed", "subtask_02": "completed", "subtask_03": "completed"})
        self.assertEqual([call for call in calls if call[0] != "Document"], [("Coach", "subtask_03")])
        self.assertEqual(result["subtask_results"]["subtask_03"]["source_version"], 2)
        self.assertEqual(result["telemetry"]["revision_count"], 1)

    def test_c_conflict_revises_the_minimal_two_subtask_scope(self):
        review_calls = []

        def reviewer(_state):
            review_calls.append(1)
            if len(review_calls) == 1:
                return {"review_v2": {"decision": "REVISE", "findings": [{
                    "id": "conflict", "subtask_ids": ["subtask_03", "subtask_04"], "severity": "HIGH",
                    "category": "conflict", "description": "numbers conflict", "evidence": [], "action": "REVISION",
                }]}}
            return {"review_v2": {"decision": "PASS", "findings": []}}

        tasks = [task("subtask_01", "performance_analysis", "completed"),
                 task("subtask_02", "career_planning", "completed"),
                 task("subtask_03", "skill_training", "completed"),
                 task("subtask_04", "nutrition_plan", "completed")]
        results = {item["id"]: completed_result(item["id"]) for item in tasks}
        graph, calls = build_harness(reviewer)
        result, _ = invoke(graph, state_for(tasks, results=results))
        self.assertCountEqual([call for call in calls if call[0] != "Document"], [
            ("Nutrition", "subtask_04"), ("Coach", "subtask_03"),
        ])
        self.assertEqual(result["plan"]["revision_targets"], [])
        self.assertEqual(result["telemetry"]["revision_count"], 1)

    def test_d_blocked_waits_for_user_and_does_not_retry_automatically(self):
        def reviewer(state):
            if state["plan"]["subtasks"][0]["status"] == "blocked":
                return {"review_v2": {"decision": "BLOCKED", "blocking_information": ["recent injury status"],
                    "findings": [{"id": "missing_input", "subtask_ids": ["subtask_01"], "severity": "HIGH",
                    "category": "missing_data", "description": "recent injury status", "evidence": [], "action": "BLOCKED"}]}}
            return {"review_v2": {"decision": "PASS", "findings": []}}

        blocked_once = {"count": 0}
        def output(_state):
            blocked_once["count"] += 1
            return ({"status": "BLOCKED", "blocked_reason": "recent injury status", "result": ""}
                    if blocked_once["count"] == 1 else complete_output("subtask_01"))

        graph, calls = build_harness(reviewer, outputs={"subtask_01": output})
        config = {"configurable": {"thread_id": f"loop-test-{uuid.uuid4()}"}}
        graph.invoke(state_for([task("subtask_01")]), config)
        self.assertIn("human_input", graph.get_state(config).next)
        self.assertEqual(blocked_once["count"], 1)
        graph.invoke(Command(resume={"information": "无近期伤病，允许常规训练"}), config)
        result = graph.get_state(config).values
        self.assertEqual(blocked_once["count"], 2)
        self.assertEqual(result["final_report"], "final report")
        self.assertEqual(result["user_context"]["human_inputs"], ["无近期伤病，允许常规训练"])
        self.assertIn(("Coach", "subtask_01"), calls)

    def test_e_refuted_hypothesis_enters_replan_and_records_llm_usage(self):
        review_calls = []

        def reviewer(_state):
            review_calls.append(1)
            if len(review_calls) == 1:
                return {"review_v2": {"decision": "REPLAN", "findings": [{
                    "id": "h1_refuted", "subtask_ids": ["subtask_01"], "severity": "CRITICAL",
                    "category": "hypothesis", "description": "baseline hypothesis refuted",
                    "reason": "new observation contradicts h1", "evidence": ["observation"], "action": "REPLAN",
                }]}}
            return {"review_v2": {"decision": "PASS", "findings": []}}

        llm = FakeLLM({
            "reason": "h1 is refuted; collect replacement evidence", "mission_changed": False,
            "mission_change_reason": "", "mission_patch": {},
            "hypotheses": [
                {"id": "h1", "statement": "original assumption is rejected", "confidence": 0.1,
                 "status": "rejected", "supporting_evidence": [], "contradicting_evidence": ["observation"]},
                {"id": "h2", "statement": "new explanation", "confidence": 0.7,
                 "status": "open", "supporting_evidence": [], "contradicting_evidence": []},
            ],
            "plan": {"objective": "produce a safe recommendation", "information_gaps": [], "subtasks": [
                {"id": "subtask_01", "goal": "subtask_01 objective", "purpose": "produce reviewable evidence",
                 "capability": "performance_analysis", "priority": 1, "depends_on": [], "status": "pending"},
                {"id": "subtask_02", "goal": "replacement evidence", "purpose": "test h2",
                 "capability": "skill_training", "priority": 2, "depends_on": [], "status": "pending"},
            ]},
        })
        initial_task = task("subtask_01", "performance_analysis", "completed")
        graph, calls = build_harness(reviewer, manager_llm=llm)
        result, _ = invoke(graph, state_for([initial_task], hypotheses=[{
            "id": "h1", "statement": "original assumption", "confidence": .8, "status": "open",
            "supporting_evidence": [], "contradicting_evidence": [],
        }], results={"subtask_01": completed_result("subtask_01")}))
        self.assertEqual(result["plan"]["version"], 2)
        self.assertEqual(result["telemetry"]["replan_count"], 1)
        self.assertEqual(result["telemetry"]["llm_call_count"], 1)
        self.assertEqual(result["telemetry"]["input_tokens"], 11)
        self.assertEqual(result["telemetry"]["output_tokens"], 7)
        self.assertEqual([call for call in calls if call[0] != "Document"], [("Coach", "subtask_02")])
        self.assertEqual({item["id"]: item["status"] for item in result["hypotheses"]}["h1"], "rejected")

    def test_f_low_finding_is_kept_and_cannot_start_an_infinite_revision_loop(self):
        def reviewer(state):
            return {"review_v2": normalise_review_result({"decision": "REVISE", "findings": [{
                "id": "style", "subtask_ids": ["subtask_01"], "severity": "LOW", "category": "style",
                "description": "non-material wording preference", "evidence": [], "action": "REVISION",
            }]}, state.get("subtasks", []))}

        initial = task("subtask_01", "skill_training", "completed")
        graph, _ = build_harness(reviewer)
        result, _ = invoke(graph, state_for([initial], results={"subtask_01": completed_result("subtask_01")}))
        self.assertEqual(result["review_v2"]["decision"], "PASS")
        self.assertEqual(result["telemetry"]["review_count"], 1)
        self.assertEqual(result["telemetry"]["revision_count"], 0)
        self.assertEqual(result["final_report"], "final report")


if __name__ == "__main__":
    unittest.main()
