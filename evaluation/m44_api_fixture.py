"""An isolated HTTP owner used only by the explicit CLI/API integration test."""
import argparse
from pathlib import Path
from backend.process_supervisor import await_start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_relative_to(Path(__file__).resolve().parents[1] / 'local_data/game_imports/m4-implementation'):
        raise ValueError('Only an isolated implementation root is supported')
    await_start()
    from backend.main import create_app
    from backend.process_runner import ProcessRunner
    from evaluation.player_fixtures import write_fixture
    from player_data.repository import FixtureRepository
    import uvicorn
    write_fixture(root / 'player', speed=44)
    app = create_app(data_dir=root / 'tasks', player_repository=FixtureRepository(root / 'player'), demo=False,
        runner=ProcessRunner('evaluation.m43_worker'))
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='error')


if __name__ == '__main__':
    main()
