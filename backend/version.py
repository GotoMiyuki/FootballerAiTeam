"""Product version shared by the API description and health response."""
from pathlib import Path

APP_VERSION = (Path(__file__).resolve().parents[1] / 'VERSION').read_text(encoding='utf-8').strip()
