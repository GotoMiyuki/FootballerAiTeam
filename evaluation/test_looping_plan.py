"""Regression tests for the Mission/Hypothesis/Plan/Review control loop."""

import json
import unittest

from agents.manager import ManagerAgent
from agents.reviewer import ReviewerAgent
from graph import (
    _ready_subtask,
    create_initial_state,
    merge_observations,
    route_after_assess,
)
from loop_contracts import build_v2_state_patch


class LoopingPlanTests(unittest.TestCase):
    def test_ready_subtask_honours_dependencies_and_priority(self):
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

    def test_plan_allows_multiple_tasks_for_one_capability(self):
        mission = {"objective": "prepare", "constraints": []}
        raw = {"subtasks": [
            {"id": "a", "goal": "assess", "purpose": "establish baseline",
             "capability": "performance_analysis", "priority": 1, "depends_on": []},
            {"id": "b", "goal": "compare", "purpose": "test hypothesis",
             "capability": "performance_analysis", "priority": 2, "depends_on": ["a"]},
        ]}
        plan = ManagerAgent._normalise_plan(raw, mission, [])
        self.assertEqual([task["id"] for task in plan["subtasks"]], ["a", "b"])

    def test_review_updates_do_not_duplicate_observations(self):
        merged = merge_observations(
            [{"subtask_id": "a", "result": "evidence"}],
            [{"subtask_id": "a", "findings": [{"type": "validity"}], "confidence": 0.8}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["result"], "evidence")
        self.assertEqual(merged[0]["confidence"], 0.8)

    def test_reviewer_returns_subtask_scoped_static_finding(self):
        state = create_initial_state("test")
        state["mission"] = {"primary_goal": "goal", "domain_contributions": {}}
        state["plan"] = {"subtasks": [
            {"id": "missing", "capability": "skill_training", "priority": 1,
             "depends_on": [], "status": "failed"},
        ]}
        result = ReviewerAgent(llm=None).run(state)
        finding = result["review"]["findings"][0]
        self.assertEqual(result["review"]["status"], "needs_revision")
        self.assertEqual(finding["subtask_id"], "missing")
        self.assertIn(finding["action"], {"revise_subtask", "request_more_evidence"})
        self.assertEqual(result["plan"]["subtasks"][0]["status"], "needs_revision")

    def test_explicit_termination_routes_to_synthesis(self):
        state = create_initial_state("test")
        state["termination_reason"] = "max_iterations"
        self.assertEqual(route_after_assess(state), "Document")

    def test_replan_preserves_accepted_subtasks(self):
        class Response:
            content = json.dumps({
                "reason": "repair one finding",
                "reopen_subtask_ids": ["bad"],
                "skip_subtask_ids": [],
                "new_subtasks": [],
                "termination_reason": "",
            })

        class LLM:
            def invoke(self, _messages):
                return Response()

        state = create_initial_state("test")
        state["mission"] = {"objective": "goal", "output_type": "report"}
        state["plan"] = {"version": 1, "subtasks": [
            {"id": "good", "goal": "accepted", "purpose": "p",
             "capability": "performance_analysis", "priority": 1,
             "depends_on": [], "status": "completed"},
            {"id": "bad", "goal": "revise", "purpose": "p",
             "capability": "skill_training", "priority": 2,
             "depends_on": [], "status": "completed"},
        ]}
        state["review"] = {"status": "needs_revision", "findings": [{
            "subtask_id": "bad", "type": "validity", "severity": "high",
            "description": "bad evidence", "action": "recalculate",
        }]}
        result = ManagerAgent(LLM())._apply_replan(state, [])
        statuses = {task["id"]: task["status"] for task in result["plan"]["subtasks"]}
        self.assertEqual(statuses, {"good": "completed", "bad": "pending"})
        self.assertNotIn("Analyst", result["domain_outputs"])
        self.assertIn("Coach", result["domain_outputs"])

    def test_v2_contract_migrates_legacy_plan_hypothesis_and_review(self):
        patch = build_v2_state_patch({
            "mission": {"objective": "prepare", "constraints": ["avoid overload"],
                        "context": {"information_gaps": ["sleep data"]}},
            "plan": {"version": 2, "subtasks": [{
                "id": "s1", "goal": "assess readiness", "capability": "performance_analysis",
                "depends_on": [], "status": "needs_revision",
            }]},
            "hypotheses": [{"id": "h1", "statement": "detraining", "confidence": 0.6,
                              "status": "weakened", "supporting_evidence": [], "contradicting_evidence": []}],
            "review": {"status": "needs_revision", "findings": [{
                "subtask_id": "s1", "severity": "high", "type": "validity",
                "description": "timeline conflict", "action": "revise_subtask",
            }]},
        })
        self.assertEqual(patch["subtasks"][0]["objective"], "assess readiness")
        self.assertEqual(patch["subtasks"][0]["status"], "REVISION_REQUIRED")
        self.assertEqual(patch["hypotheses_v2"][0]["status"], "WEAKENED")
        self.assertEqual(patch["review_v2"]["decision"], "REVISE")
        self.assertEqual(patch["review_v2"]["findings"][0]["action"], "REVISION")
        self.assertEqual(patch["plan_v2"]["information_gaps"], ["sleep data"])
        self.assertEqual(patch["plan_v2"]["revision_targets"], ["s1"])


if __name__ == "__main__":
    unittest.main()
