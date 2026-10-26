"""Read-only M3 readiness audit; never creates choices, observations or a milestone pass.

Reparse verified real native evidence and compare canonical imports and stored E/C chains.
Even a complete automatic chain still requires review of real model/game/feedback scope.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from game_adapters.pes2021_native_package import read_package
from game_adapters.pes2021_import import map_package, PROVIDER
from player_data.json_repository import JsonPlayerRepository
from player_data.models import PlayerContext, content_hash


def native_coverage(package, mapped, bundle):
    """Count exact fixture imports; nonappearance stays a career event, never a zero match."""
    if package['purpose'] != 'real_test' or package['identity'] != bundle['snapshot'].to_dict()['context']:
        raise ValueError('M3只能核验同context的真实原生包，不能使用fixture或其他分支')
    current = {row['source_record_id']: row for row in bundle['observations']
        if row['provider'] == PROVIDER and row['source_type'] == 'game_observation'
        and row['import_status'] == 'APPLIED' and row['current_paths']}
    by_local = {row['local_id']: row for row in mapped}
    matched, fixtures, run, longest, missing = [], [], 0, 0, []
    versions = set()
    for event in package['events']:
        planned = by_local.get(event['event_id'])
        stored = current.get(planned['source_record_id']) if planned else None
        exact = bool(planned and stored and stored['payload'] == planned['payload']
            and stored['kind'] == planned['kind'] and stored['effective_at'] == planned['effective_at']
            and stored.get('game_version') and stored['time_domain'] == 'game' and stored['time_precision'] == 'day')
        if not exact:
            missing.append(event['event_id'])
        if exact:
            versions.add(stored['game_version'])
            entry = dict(key=stored['key'], source_record_id=stored['source_record_id'], kind=stored['kind'],
                effective_at=stored['effective_at'], event_id=event['event_id'], semantic_hash=stored['semantic_hash'])
            fixtures.append(entry)
            if stored['kind'] == 'match_record':
                matched.append(entry)
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return dict(reviewed_fixture_events=len(package['events']), matched_fixture_records=len(fixtures), matched_personal_matches=len(matched),
        longest_verified_event_run=longest, minimum_fixture_records=10,
        single_game_version=len(versions) == 1, game_versions=sorted(versions), unmatched_events=missing,
        observations=fixtures, ten_verified_events=longest >= 10 and len(versions) == 1,
        continuity_scope='审阅事件清单连续，不自动证明原游戏赛程无漏场，仍须人工核对赛程')


def stored_feedback_chains(tasks_root, context):
    """Inspect existing task tables with SQLite mode=ro; no Store/service migrations or replay."""
    from backend.models import MissionView
    from career_actions.models import Recommendation, ExecutionRecord, DecisionRecord
    from career_actions.assessment_models import Assessment
    from career_actions.followups import validate, summary
    from execution_contracts import publishable
    from backend.runtime import inspect_checkpoint
    root = Path(tasks_root).resolve()
    database = root / 'workspace.sqlite3'
    if not database.is_file():
        return {'status': 'NO_TASK_DATABASE', 'chains': [], 'unverified': []}
    db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        missions = [MissionView.model_validate_json(row[0]) for row in db.execute('SELECT snapshot FROM missions')]
        missions = {item.id: item for item in missions if item.input_reference.context == context}
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {'recommendations', 'assessments', 'recommendation_actions', 'recommendation_action_heads', 'mission_inputs'}
        if not needed <= tables:
            return {'status': 'MISSING_E_C_TABLES', 'chains': [], 'unverified': []}
        chains, unverified = [], []
        for child in missions.values():
            ref = child.lineage.feedback_reference if child.lineage else None
            if not ref:
                continue
            try:
                row = db.execute('SELECT payload FROM mission_inputs WHERE mission_id=?', (child.id,)).fetchone()
                inputs = json.loads(row[0])
                digest = inputs.pop('content_hash')
                if content_hash(inputs) != digest:
                    raise ValueError('fixed input digest differs')
                packet = inputs['continuation_context']['career_feedback']
                validate(packet, context, child.input_reference.snapshot_id)
                if summary(packet) != ref or child.lineage.parent_mission_id != packet['parent_mission_id']:
                    raise ValueError('feedback lineage differs')
                parent = missions[packet['parent_mission_id']]
                if parent.input_reference.verification != 'VERIFIED' or 'game_observation' not in parent.input_reference.source_types:
                    raise ValueError('parent is not a sourced actual task')
                rec_id = ref['recommendation_id']
                record = db.execute('SELECT payload FROM recommendations WHERE id=?', (rec_id,)).fetchone()
                rec = Recommendation.model_validate_json(record[0])
                if rec.context.model_dump() != context or rec.source.mission_id != parent.id or rec.revision != ref['recommendation_revision']:
                    raise ValueError('original recommendation differs')
                assessment = packet['assessment']
                if not assessment:
                    raise ValueError('no selected observation comparison')
                row = db.execute('SELECT payload FROM assessments WHERE id=? AND revision=?',
                    (assessment['assessment_id'], assessment['revision'])).fetchone()
                original = Assessment.model_validate_json(row[0])
                if (content_hash(original.model_dump()) != assessment['source_content_hash'] or original.source != rec.source
                        or original.result.evidence_status != 'COMPLETE' or not original.result.followup_observations):
                    raise ValueError('comparison lacks complete sourced follow-up evidence')
                decision = packet['decision']
                if not decision or decision['status'] not in {'ADOPTED', 'REJECTED'}:
                    raise ValueError('no explicit adoption/rejection at submission')
                row = db.execute('SELECT payload FROM recommendation_actions WHERE id=? AND kind=?', (decision['decision_id'], 'decision')).fetchone()
                choice = DecisionRecord.model_validate_json(row[0])
                if choice.source != rec.source or choice.status != decision['status'] or content_hash(choice.model_dump()) != decision['source_content_hash']:
                    raise ValueError('choice source differs')
                usable = []
                for execution in packet['executions']:
                    row = db.execute('SELECT payload FROM recommendation_actions WHERE id=? AND kind=?', (execution['execution_id'], 'execution')).fetchone()
                    action = ExecutionRecord.model_validate_json(row[0])
                    if action.source != rec.source or content_hash(action.model_dump()) != execution['source_content_hash']:
                        raise ValueError('execution source differs')
                    if action.status != 'UNKNOWN' and action.window.time_domain != 'unknown':
                        usable.append(action.execution_id)
                if not usable:
                    raise ValueError('no explicit execution feedback with a known window')
                if child.status != 'COMPLETED' or child.delivery_status != 'PUBLISHABLE':
                    raise ValueError('adjustment has no publishable completed result')
                checkpoint = inspect_checkpoint(root / 'checkpoints.sqlite3', child.id)
                if (not publishable(checkpoint.values) or checkpoint.values.get('continuation_context') != inputs['continuation_context']
                        or checkpoint.values.get('player_snapshot') != inputs['player_snapshot']):
                    raise ValueError('completed execution source differs')
                generated = [Recommendation.model_validate_json(row[0]) for row in db.execute(
                    'SELECT payload FROM recommendations WHERE mission_id=?', (child.id,))]
                if not generated or any(item.source.mission_id != child.id or item.context.model_dump() != context for item in generated):
                    raise ValueError('no new sourced recommendation')
                chains.append(dict(parent_mission_id=parent.id, mission_id=child.id, assessment_id=original.assessment_id,
                    assessment_revision=original.revision, decision_id=choice.decision_id, execution_ids=usable,
                    new_recommendation_ids=[item.recommendation_id for item in generated],
                    limitations=['执行仍为用户自报，须复核实际范围', '真实模型/游戏范围和新建议质量需要独立审阅']))
            except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
                unverified.append({'mission_id': child.id, 'reason': str(error)})
        return {'status': 'READ_ONLY', 'chains': chains, 'unverified': unverified}
    finally:
        db.close()


def run(dataset, actual_root, tasks_root):
    package = read_package(dataset)
    context = PlayerContext(**package['identity'])
    repository = JsonPlayerRepository(actual_root, fixture_root=Path(__file__).resolve().parents[1] / 'memory', context=context)
    bundle = repository.read_observations(context)
    coverage = native_coverage(package, map_package(package, dataset), bundle)
    feedback = stored_feedback_chains(tasks_root, context.to_dict())
    gaps = []
    if not coverage['ten_verified_events']:
        gaps.append('缺少同一审阅生涯/分支中连续十场有来源赛程记录；未入选/参与未知须保留原语义，不补零比赛、不混回放或合成样本')
    if not feedback['chains']:
        gaps.append('缺少同一实际生涯的明确选择、可复核执行反馈、完整后续观察与新建议的已完成任务链')
    return dict(schema_version=1, audited_at=datetime.now(timezone.utc).isoformat(), m3_passed=False,
        status='BLOCKED' if gaps else 'AUTOMATED_EVIDENCE_READY_REQUIRES_REVIEW', context=context.to_dict(),
        coverage=coverage, feedback=feedback, blockers=gaps,
        manual_review=['确认真实模型调用与建议质量范围', '确认玩家明确选择及实际执行反馈', '确认连续记录未混入回放/回档分支'],
        writes={'game': 0, 'actual_repository': 0, 'task_database': 0},
        limitations=['本审计不代替真实生涯验收，不自动授予M3通过，不生成行动或观察'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--actual-root', type=Path, required=True)
    parser.add_argument('--tasks-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.dataset, args.actual_root, args.tasks_root)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('m3_passed', 'status', 'context', 'blockers')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
