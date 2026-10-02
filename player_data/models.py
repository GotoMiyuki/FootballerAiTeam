from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re

SCHEMA_VERSION = '1'


class PlayerDataError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PlayerContext:
    career_id: str = 'demo-career'
    branch_id: str = 'main'
    player_id: str = 'demo-player'

    def __post_init__(self):
        for identity in (self.career_id, self.branch_id, self.player_id):
            if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identity):
                raise PlayerDataError('invalid_identity', 'Invalid player context identity')

    def to_dict(self):
        return {'career_id': self.career_id, 'branch_id': self.branch_id, 'player_id': self.player_id}


def content_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


class PlayerSnapshot:
    """Copy-on-read view; no caller can mutate the repository or another consumer."""
    def __init__(self, context, profile, training, matches, career, metadata):
        self._data = deepcopy({'context': context.to_dict(), 'profile': profile, 'training': training,
                               'matches': matches, 'career': career, 'metadata': metadata})

    def to_dict(self):
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, value):
        return cls(PlayerContext(**value['context']), value['profile'], value['training'],
                   value['matches'], value['career'], value['metadata'])

    @property
    def profile(self):
        return deepcopy(self._data['profile'])
    @property
    def training(self):
        return deepcopy(self._data['training'])
    @property
    def matches(self):
        return deepcopy(self._data['matches'])
    @property
    def career(self):
        return deepcopy(self._data['career'])
    @property
    def metadata(self):
        return deepcopy(self._data['metadata'])
