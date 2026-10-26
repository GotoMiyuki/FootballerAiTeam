"""Pure frozen-memory validation and task scoping; never reads live repositories."""
from copy import deepcopy
import re
from player_data.models import content_hash

POLICY = 'career-memory-selection-v1'
CAPABILITIES = {
    'skill_training': ('训练', '速度', '力量', '技术', '恢复', '体能', '分钟', '时间', 'training', 'speed', 'coach'),
    'performance_analysis': ('比赛', '评分', '进球', '助攻', '表现', '出场', 'rating', 'match', 'performance'),
    'nutrition_plan': ('营养', '饮食', '蛋白', '热量', '补剂', '餐食', 'nutrition', 'protein', 'calories'),
    'career_planning': ('生涯', '职业', '合同', '续约', '俱乐部', 'career', 'contract'),
    'transfer_analysis': ('转会', '身价', '目标球队', 'transfer', 'club'),
}
STOP_WORDS = {'安排', '计划', '希望', '目标', '每天', '每周', '需要', '我们', '根据', '情况', '进行',
              '相关', '记录', '当前', '用户', '确认', '可以', '新的', 'the', 'and', 'for', 'with'}


def tokens(text):
    text = text.lower()
    values = set(re.findall(r'[a-z0-9_]+', text))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        values.update(run[index:index + 2] for index in range(len(run) - 1))
    return values - STOP_WORDS


def capabilities(text):
    lowered = text.lower()
    return {capability for capability, words in CAPABILITIES.items() if any(word in lowered for word in words)}


def seal(packet):
    packet['content_hash'] = content_hash({key: value for key, value in packet.items() if key != 'content_hash'})
    return packet


def validate(packet, context=None):
    if not packet:
        return
    if packet.get('schema_version') != 1 or packet.get('policy_version') != POLICY:
        raise ValueError('固定记忆格式或选择规则不兼容，不能读取最新记忆补齐')
    if packet.get('content_hash') != content_hash({key: value for key, value in packet.items() if key != 'content_hash'}):
        raise ValueError('固定记忆完整性校验失败')
    if context is not None and packet.get('context') != context:
        raise ValueError('固定记忆与任务context不一致')
    if not isinstance(packet.get('items'), list):
        raise ValueError('固定记忆集合缺失')
    for item in packet['items']:
        if item.get('context') != packet.get('context') or item.get('content_hash') != content_hash({key: value for key, value in item.items() if key != 'content_hash'}):
            raise ValueError('固定记忆条目身份或摘要不一致')


def task_items(state, task):
    packet = state.get('career_memory_context') or {}
    validate(packet, (state.get('player_snapshot') or {}).get('context'))
    goal_tokens = tokens(str(task.get('goal') or task.get('objective') or '') + ' ' + str(task.get('purpose') or ''))
    selected = []
    for item in packet.get('items', []):
        scopes = item['capabilities']
        if task.get('capability') in scopes or not scopes and goal_tokens & tokens(item['content']):
            selected.append(deepcopy(item))
    return selected


def manager_context(packet):
    validate(packet)
    if not packet:
        return {}
    return {'policy_version': packet['policy_version'], 'context': packet['context'],
        'items': deepcopy(packet['items']), 'limitations': deepcopy(packet['limitations']),
        'omitted_relevant': [item for item in packet['omitted'] if item['reason'] != 'irrelevant']}
