"""Explicit, digest-checked relocation of immutable native source directories."""
import hashlib
import json
import os
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def local_file(root, relative):
    name = Path(relative)
    if name.is_absolute() or '..' in name.parts or not name.parts:
        raise ValueError('归档路径必须位于声明目录内')
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('归档路径越出声明目录')
    return path


def directory_files(root, *, skip_suffixes=()):
    root = Path(root).resolve(strict=True)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('来源目录不支持链接或越界路径')
        if path.is_file():
            if path.name.endswith(skip_suffixes):
                continue
            files[path.relative_to(root).as_posix()] = file_hash(path)
    return files


def tree_hash(root):
    from player_data.models import content_hash
    return content_hash(directory_files(root))


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


class SourceMap:
    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.base = self.path.parent
        self.value = json.loads(self.path.read_text(encoding='utf-8'))
        if self.value.get('schema_version') != 1:
            raise ValueError('来源路径映射版本不兼容')
        self.raw_root = local_file(self.base, self.value['raw_root'])

    def dataset(self, requested):
        key = path_key(requested)
        matches = [row for row in self.value['datasets'] if key in {
            path_key(row['original']), path_key(local_file(self.base, row['path']))}]
        if len(matches) != 1:
            raise ValueError('来源未被唯一映射；请使用清单中的原路径或恢复路径')
        row = matches[0]
        physical = local_file(self.base, row['path'])
        if not physical.resolve().is_relative_to(self.raw_root.resolve()) or tree_hash(physical) != row['tree_sha256']:
            raise ValueError('恢复来源路径或目录摘要不一致')
        return Path(row['original']), physical, self.raw_root
