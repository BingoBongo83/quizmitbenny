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

# Optional: site-wide password – protects every page except /audience.
# Guests enter it at /unlock; empty = no protection.
SITE_PASSWORD = ""

# Token the local buzzer bridge (scripts/buzzer_bridge.py) uses to authenticate.
BUZZER_TOKEN = "buzzer-token"

# ---------- Question seeding ----------
# Azure OpenAI – preferred translator.
# AZURE_OPENAI_MODEL is the *deployment name* in your Azure resource.
AZURE_OPENAI_API_KEY = ""
AZURE_OPENAI_ENDPOINT = "https://<resource>.openai.azure.com/"
AZURE_OPENAI_MODEL = ""
AZURE_OPENAI_API_VERSION = "2025-04-01-preview"

# OpenAI API key – used when no Azure deployment is configured
# (https://platform.openai.com/api-keys)
OPENAI_API_KEY = ""
OPENAI_MODEL = "gpt-4o-mini"

# DeepL API key – fallback when neither Azure nor OpenAI is configured
# (free key: https://www.deepl.com/pro-api)
DEEPL_API_KEY = ""
