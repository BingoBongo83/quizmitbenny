"""Loads config.py from the project root (gitignored, see example.config.py).

Environment variable DATABASE_URL still wins over everything (used by tests).
"""

import importlib.util
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

_cfg_path = BASE_DIR / "config.py"
if _cfg_path.exists():
    _spec = importlib.util.spec_from_file_location("quiz_local_config", _cfg_path)
    _cfg = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cfg)
else:
    _cfg = None


def _get(name, default=""):
    if _cfg is not None:
        return getattr(_cfg, name, default)
    return default


def get_database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    if url := _get("DATABASE_URL"):
        return url
    host = _get("DB_HOST", "localhost")
    if not _get("DB_PASSWORD") and not _get("DB_USER"):
        return "sqlite:///./quiz_dev.db"
    return (
        f"mysql+pymysql://{_get('DB_USER')}:{_get('DB_PASSWORD')}"
        f"@{host}:{_get('DB_PORT', 3306)}/{_get('DB_NAME', 'quiz')}?charset=utf8mb4"
    )


DATABASE_URL = get_database_url()
SECRET_KEY = _get("SECRET_KEY", "dev-secret-key")
BUZZER_TOKEN = _get("BUZZER_TOKEN", "")
SITE_PASSWORD = _get("SITE_PASSWORD", "")

# Translation: Azure OpenAI -> OpenAI -> DeepL (env vars win over config.py)
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY") or _get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = (
    os.environ.get("AZURE_OPENAI_ENDPOINT") or _get("AZURE_OPENAI_ENDPOINT", "")
).rstrip("/")
AZURE_OPENAI_MODEL = os.environ.get("AZURE_OPENAI_MODEL") or _get("AZURE_OPENAI_MODEL", "")
AZURE_OPENAI_API_VERSION = (
    os.environ.get("AZURE_OPENAI_API_VERSION")
    or _get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or _get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or _get("OPENAI_MODEL", "gpt-4o-mini")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY") or _get("DEEPL_API_KEY", "")
