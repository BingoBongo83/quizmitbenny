# Quiz

Quiz-App mit Buzzern (Arduino) — Web-App auf eigenem Server.

## Architektur

- **FastAPI** (REST + WebSocket), deploybar hinter nginx — oder lokal zum Testen
- **MariaDB** (oder SQLite ohne Konfiguration zum Entwickeln)
- Drei Views, komplett deutsch:
  - `/board` – Scoreboard für Beamer/TV (öffentlich)
  - `/admin` – Moderator-Panel (Login nötig)
  - `/einstellungen` – Spieler, Runden, Skill-Level, Punkte-Regeln, Fragen
- **Buzzer**: zwei Wege
  - Web Serial direkt im Admin-Panel (Chrome/Edge, HTTPS nötig)
  - `scripts/buzzer_bridge.py` auf dem Moderator-Rechner (beliebiger Browser)

Arduino-Protokoll wie bei musikquiz: Arduino sendet Buzzer-Nummer (1–5) als Zeile;
Server schickt `5` (Reset), `9` (alle aktiv), `0`–`4` (Slot gesperrt),
`G` (richtig), `R` (falsch) zurück.

## Spielablauf

Vorrunden (3–5, je 3–5 Spieler) → ggf. Playoff → 2 Halbfinals → ggf. Playoff →
Finale. Direktqualifikation: Top 2 pro Runde (bzw. automatisch angepasst, wenn
die Plätze nicht aufgehen); Restplätze per Playoff der Bestplatzierten.

Fragen haben Skill 1–5; pro Rundentyp sind im Setup aktivierbare Level
einstellbar, Fragen werden zufällig gezogen. Punkte-Regeln konfigurierbar
(Default musikquiz-Style: richtig +2; falsch → alle anderen +2, Spieler für
nächste Frage gesperrt).

Ablauf pro Frage: „Frage zeigen“ → Buzzer scharf → Spieler buzzert → Moderator
klickt die genannte Antwort → automatische Wertung + Auflösung auf dem Board.

## Setup (lokal / dev)

```bash
python3 -m venv env && source env/bin/activate
pip install -r requirements.txt
cp .env.example .env          # QUIZ_ADMIN_PASSWORD + SECRET_KEY setzen
cp database.ini.example database.ini   # MariaDB-Zugang eintragen
# oder ohne MariaDB: DATABASE_URL=sqlite:///./quiz_dev.db in .env

mysql -u quiz -p quiz < /dev/null  # DB/User in MariaDB anlegen:
# CREATE DATABASE quiz; CREATE USER 'quiz'@'localhost' IDENTIFIED BY '...';
# GRANT ALL PRIVILEGES ON quiz.* TO 'quiz'@'localhost'; FLUSH PRIVILEGES;

uvicorn app.main:app --port 8765
```

- Scoreboard: http://localhost:8765/board
- Admin: http://localhost:8765/admin (Login mit `QUIZ_ADMIN_PASSWORD`)

## Buzzer

Im Admin-Panel: „Buzzer verbinden (Web Serial)“ — nur Chrome/Edge, HTTPS nötig.

Alternativ die Bridge auf dem Rechner mit dem Arduino:

```bash
pip install pyserial websockets
python -m scripts.buzzer_bridge \
    --port /dev/cu.usbserial-XXXX \
    --server wss://quiz.example.com \
    --token <QUIZ_BUZZER_TOKEN oder Admin-Passwort>
```

Test ohne Hardware:

```bash
python -m scripts.simulate_buzzer --server ws://localhost:8765 --token changeme
```

## Fragen

- CRUD in den Einstellungen
- CSV-Import (`;`-getrennt: `text;a1;a2;a3;a4;correct;skill;category`) oder JSON
- OpenTriviaDB-Seed mit DeepL-Übersetzung: `DEEPL_API_KEY` in `.env`, dann
  `python -m scripts.seed_questions --amount 50` oder Button im UI

## Deploy (Server)

1. Repo auf Server, venv + requirements installieren
2. `.env` + `database.ini` setzen
3. `deploy/quiz.service` → `/etc/systemd/system/quiz.service`, `systemctl enable --now quiz`
4. `deploy/nginx.example` als nginx-site (WebSocket-Header sind schon drin)

## Tests

```bash
./env/bin/python -m scripts.smoke_test   # Spiellogik (SQLite-Test-DB)
./env/bin/python scripts/ws_test.py      # E2E gegen laufenden Server :8765
```
