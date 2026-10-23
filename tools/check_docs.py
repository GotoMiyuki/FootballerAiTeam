"""Check local Markdown links/anchors without network or application side effects.

Run from any directory: python tools/check_docs.py
Fenced examples and remote URLs are excluded; docs .txt sources are checked too.
"""
from __future__ import annotations

import re
import sys
import unicodedata
import hashlib
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'!?\[[^\]\n]*\]\(([^\n]*?)\)')
PRESERVED = re.compile(
    r'<!-- preserved-body-sha256: ([a-f0-9]{64}) -->\n'
    r'<!-- preserved-source-begin: ([\w-]+) -->\n'
    r'(.*?)<!-- preserved-source-end: \2 -->', re.DOTALL)


def visible_lines(text):
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            if fence is None:
                fence = (marker.group(1)[0], len(marker.group(1)))
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1]:
                fence = None
            continue
        if fence is None:
            yield number, line


def slug(text):
    text = re.sub(r'<[^>]*>', '', text).lower()
    text = re.sub(r'&[a-zA-Z]+;', '', text)
    return ''.join(c for c in text if c in '-_' or c.isspace() or not unicodedata.category(c).startswith(('P', 'S', 'C'))).replace(' ', '-')


def anchors(text):
    result, counts = set(), defaultdict(int)
    for _, line in visible_lines(text):
        result.update(re.findall(r'\bid=["\']([^"\']+)["\']', line))
        match = re.match(r'^#{1,6}\s+(.+?)\s*#*$', line)
        if match:
            s = slug(match.group(1)); n = counts[s]; counts[s] += 1
            result.add(s + (f'-{n}' if n else ''))
    return result


def main():
    documents = sorted([*ROOT.joinpath('docs').rglob('*.md'), *ROOT.joinpath('docs').rglob('*.txt'), ROOT / 'README.md', ROOT / 'game_adapters/README.md'])
    anchor_cache, errors, checked, preserved = {}, [], 0, 0
    for doc in documents:
        text = doc.read_text(encoding='utf-8-sig')
        blocks = list(PRESERVED.finditer(text))
        if len(blocks) != text.count('<!-- preserved-source-begin:') or len(blocks) != text.count('<!-- preserved-source-end:'):
            errors.append(f'{doc.relative_to(ROOT).as_posix()}: preservation marker/hash metadata mismatch')
        for block in blocks:
            preserved += 1
            if hashlib.sha256(block.group(3).encode('utf-8')).hexdigest() != block.group(1):
                errors.append(f'{doc.relative_to(ROOT).as_posix()}: historical source changed: {block.group(2)}')
        for number, line in visible_lines(text):
            for match in LINK.finditer(line):
                target = match.group(1).strip()
                if re.match(r'^(?:[a-zA-Z][\w+.-]*:|//)', target):
                    continue
                if target.startswith('<'):
                    target = target[1:target.find('>')]
                else:
                    target = re.sub(r'\s+["\'][^"\']*["\']$', '', target)
                path, _, fragment = target.partition('#')
                dest = (doc.parent / unquote(path)).resolve() if path else doc
                checked += 1
                error = None
                if not dest.exists():
                    error = 'missing target'
                elif fragment and dest.is_file() and dest.suffix in {'.md', '.txt'}:
                    if dest not in anchor_cache:
                        anchor_cache[dest] = anchors(dest.read_text(encoding='utf-8-sig'))
                    if unquote(fragment) not in anchor_cache[dest]:
                        error = 'missing anchor'
                if error:
                    errors.append(f'{doc.relative_to(ROOT).as_posix()}:{number}: {error}: {target}')
    for error in errors:
        print(error)
    print(f'{len(documents)} documents, {checked} local links, {preserved} preserved sources, {len(errors)} errors')
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
