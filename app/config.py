import os
from configparser import ConfigParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file():
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_env_file()


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    ini_path = BASE_DIR / "database.ini"
    if ini_path.exists():
        parser = ConfigParser()
        parser.read(ini_path)
        if parser.has_section("mysql"):
            p = parser["mysql"]
            return (
                f"{p.get('driver', 'mysql+pymysql')}://{p['username']}:{p['password']}"
                f"@{p['hostname']}:{p.get('port', '3306')}/{p['database']}?charset=utf8mb4"
            )
    return "sqlite:///./quiz_dev.db"


DATABASE_URL = get_database_url()
ADMIN_PASSWORD = os.environ.get("QUIZ_ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("QUIZ_SECRET_KEY", "dev-secret-key")
BUZZER_TOKEN = os.environ.get("QUIZ_BUZZER_TOKEN", "")
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
