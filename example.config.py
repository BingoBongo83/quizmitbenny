# config.py – copy to config.py and fill in your values.
# config.py is gitignored and must never be committed.

# ---------- Database ----------
# Option A: full SQLAlchemy URL (wins if set). For local dev without MariaDB:
# DATABASE_URL = "sqlite:///./quiz_dev.db"
DATABASE_URL = ""

DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "quiz"
DB_USER = "quiz"
DB_PASSWORD = "password"

# ---------- Web ----------
# Session cookie secret – generate: openssl rand -hex 32
SECRET_KEY = "changeme"

# Token the local buzzer bridge (scripts/buzzer_bridge.py) uses to authenticate.
BUZZER_TOKEN = "buzzer-token"

# ---------- Question seeding ----------
# DeepL API key (free key: https://www.deepl.com/pro-api)
DEEPL_API_KEY = ""
