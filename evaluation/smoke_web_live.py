"""Opt-in live model/API smoke test with an isolated copy of player memory.

Run from the repository root: venv/Scripts/python.exe -m evaluation.smoke_web_live
This uses configured model credentials; detailed core logs stay in .web-test-data.
"""
import contextlib
import shutil
import time
from pathlib import Path
from fastapi.testclient import TestClient

def main():
    root = Path(__file__).resolve().parent.parent
    data = root / '.web-test-data' / f'live-{int(time.time())}'
    memory = data / 'player-memory'
    memory.mkdir(parents=True)
    for name in ['player.json', 'training_history.json', 'match_history.json', 'career_history.json']:
        shutil.copy2(root / 'memory' / name, memory / name)
    from player_data.repository import FixtureRepository
    repository = FixtureRepository(memory)
    from backend.main import create_app
    objective = '请根据现有球员档案给出三条简短的技术特点总结；只分析，不制定训练计划，不修改球员档案，不联网。'
    with (data / 'runtime.log').open('w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), TestClient(create_app(data_dir=data, demo=False, player_repository=repository)) as client:
            response = client.post('/api/messages', json={'conversation_id': 'conv_live_smoke', 'content': objective})
            response.raise_for_status()
            mission_id = response.json()['mission_id']
            deadline = time.monotonic() + 420
            states = []
            supplied = 0
            while time.monotonic() < deadline:
                mission = client.get(f'/api/missions/{mission_id}').json()
                if not states or states[-1] != mission['status']:
                    states.append(mission['status'])
                if mission['status'] == 'BLOCKED' and mission_id not in client.app.state.service.active:
                    if mission['blocked']['reason'] == 'report_approval':
                        client.post(f'/api/missions/{mission_id}/input', json={'values': {'approved': True}}).raise_for_status()
                    elif supplied == 0:
                        client.post(f'/api/missions/{mission_id}/input', json={'values': {'information': '这是基于档案的静态技术特点总结，缺失的当前恢复信息无需推断。请只总结档案已知属性并标记不确定项。'}}).raise_for_status()
                        supplied += 1
                    else:
                        break
                if mission['status'] in {'COMPLETED', 'FAILED'}:
                    break
                time.sleep(0.5)
            (data / 'summary.json').write_text(__import__('json').dumps({'mission_id': mission_id, 'status': mission['status'], 'states': states, 'plan_version': mission['plan']['version'] if mission.get('plan') else None, 'telemetry': mission['telemetry']}, ensure_ascii=False, indent=2), encoding='utf-8')
            assert mission['status'] == 'COMPLETED', f"Live task stopped in {mission['status']}"
            report = client.get(f'/api/missions/{mission_id}/report')
            report.raise_for_status()
            (data / 'report.md').write_text(report.json()['markdown'], encoding='utf-8')
            assert len(report.json()['markdown']) > 30
    print(f'Live API smoke passed: {mission_id}; states={states}; output={data}')

if __name__ == '__main__':
    main()
