"""Explicit test fixture initialization, never used to initialize actual storage."""
import json
from pathlib import Path


def write_fixture(root, name='Isolated Test Player', speed=44):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    profile = {'name': name, 'age': 22, 'height': 180, 'weight': 75, 'position': 'ST', 'overall': 66,
               'training_intensity': 'Medium', 'injury': 'None', 'club': 'Fixture FC',
               'attributes': {'physical': {'speed': speed, 'stamina': None}, 'offense': {'shooting': 0}},
               'other_features': {'form_consistency': 4, 'injury_resistance': 3, 'weak_foot_accuracy': 2}}
    for filename, data in (('player.json', profile), ('training_history.json', [{'week': '2026-W20', 'weekly_load': 12, 'avg_rpe': None}]),
                           ('match_history.json', [{'date': '2026-05-20', 'goals': 0, 'rating': None}]),
                           ('career_history.json', {'market_value_history': [{'date': '2026-05', 'estimated_value_eur': 5000}]})):
        (root / filename).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return profile
