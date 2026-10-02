"""Execute the production CLI in a fresh process with deterministic graph nodes only."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from evaluation import test_session_semantics as semantics
from player_data.repository import FixtureRepository


def main():
    import app
    harness = semantics.SessionSemanticsTests()
    harness.calls = []
    harness.require_input = os.getenv('FAIT_TEST_REQUIRE_INPUT') == '1'
    repository = FixtureRepository(Path(os.environ['FAIT_TEST_PLAYER_ROOT']))
    original_read = repository.read_snapshot

    def read(*args, **kwargs):
        if os.getenv('FAIT_TEST_NO_LATEST') == '1':
            raise AssertionError('Resume/history must not read latest player input')
        return original_read(*args, **kwargs)

    def runtime(*args):
        if os.getenv('FAIT_TEST_READ_ONLY') == '1':
            raise AssertionError('History must not construct an executable runtime')
        return harness.runtime(*args)

    def check():
        if os.getenv('FAIT_TEST_READ_ONLY') == '1':
            raise AssertionError('History must not require model configuration')
        return True

    try:
        with patch('backend.runtime.create_runtime', side_effect=runtime), patch.object(app, 'check_config', side_effect=check), \
                patch('player_data.repository.get_repository', return_value=repository), \
                patch.object(repository, 'read_snapshot', side_effect=read), \
                patch('utils.helpers.create_llm', side_effect=AssertionError('No live model allowed')):
            return app.main()
    finally:
        with Path(os.environ['FAIT_TEST_CALLS']).open('a', encoding='utf-8') as output:
            for name, version in harness.calls:
                output.write(json.dumps({'pid': os.getpid(), 'node': name, 'version': version}) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
