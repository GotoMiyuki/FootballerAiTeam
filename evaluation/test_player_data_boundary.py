"""Offline fact-write boundary checks against real Coach and tool allowlists."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.coach import CoachAgent
from config import config
from tools import ALL_TOOLS, COACH_TOOLS, CAREER_TOOLS, ANALYST_TOOLS, NUTRITION_TOOLS
from tools.database import DATABASE_TOOLS


class FactBoundaryTests(unittest.TestCase):
    def test_all_model_tool_sets_are_read_only(self):
        for group in (ALL_TOOLS, COACH_TOOLS, CAREER_TOOLS, ANALYST_TOOLS,
                      NUTRITION_TOOLS, DATABASE_TOOLS):
            self.assertFalse(any(t.name.startswith(('Update', 'Append')) for t in group))

    def test_real_coach_predictions_and_revisions_leave_files_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for key in ('PLAYER_FILE', 'TRAINING_HISTORY_FILE', 'MATCH_HISTORY_FILE', 'CAREER_HISTORY_FILE'):
                path = Path(directory) / (key + '.json')
                path.write_text('{}', encoding='utf-8')
                paths[key] = str(path)
            before = {p: Path(p).read_bytes() for p in paths.values()}
            coach = CoachAgent(None)
            state = {'player_profile': {'attributes': {'physical': {'speed': 80}}},
                     'mission': {'domain_contributions': {'Coach': {'needed': True}}}}
            with patch.multiple(config, **paths), patch.object(coach, '_run_react_loop', return_value=(
                json.dumps({**__import__('evaluation.test_looping_plan', fromlist=['complete_output']).complete_output('fixture'), 'attribute_update_suggestions': {'physical': {'speed': 83}}}), [])):
                for _ in range(2):
                    output = coach.run(state)
                    self.assertIn('83', output['domain_outputs']['Coach'])
                coach._execute_tool('UpdatePlayerAttributeTool', {'update_json': '{"physical":{"speed":99}}'})
            self.assertEqual(before, {p: Path(p).read_bytes() for p in paths.values()})


if __name__ == '__main__':
    unittest.main()
