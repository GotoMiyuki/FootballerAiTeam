"""Check release metadata without importing the application or installing packages."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    expected = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    package = json.loads((ROOT / 'frontend/package.json').read_text(encoding='utf-8'))
    lock = json.loads((ROOT / 'frontend/package-lock.json').read_text(encoding='utf-8'))
    values = {'frontend/package.json': package['version'],
              'frontend/package-lock.json': lock['version'],
              'frontend/package-lock.json packages[""]': lock['packages']['']['version']}
    errors = [f'{path}: {value} != VERSION {expected}' for path, value in values.items() if value != expected]
    for error in errors:
        print(error)
    print(f'Product version {expected}: {len(values)} package declarations, {len(errors)} errors')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
