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
cp example.config.py config.py   # Zugangsdaten eintragen (gitignored!)
```

`config.py` enthält alle Secrets: MariaDB-Zugang (`DB_*` oder `DATABASE_URL`),
`SECRET_KEY`, `BUZZER_TOKEN`, `DEEPL_API_KEY`. Ohne MariaDB einfach
`DATABASE_URL = "sqlite:///./quiz_dev.db"` setzen.

MariaDB anlegen:
```sql
CREATE DATABASE quiz;
CREATE USER 'quiz'@'localhost' IDENTIFIED BY '...';
GRANT ALL PRIVILEGES ON quiz.* TO 'quiz'@'localhost';
FLUSH PRIVILEGES;
```

Admin-Benutzer anlegen (Credentials liegen in der Datenbank):

```bash
python -m scripts.admin create benny     # Passwort wird abgefragt
python -m scripts.admin passwd benny     # Passwort zurücksetzen
python -m scripts.admin list
python -m scripts.admin delete benny
```

Starten:

```bash
uvicorn app.main:app --port 8765
```

- Scoreboard: http://localhost:8765/board
- Admin: http://localhost:8765/admin (Login mit DB-Benutzer)

## Buzzer

Im Admin-Panel: „Buzzer verbinden (Web Serial)“ — nur Chrome/Edge, HTTPS nötig.

Alternativ die Bridge auf dem Rechner mit dem Arduino:

```bash
pip install pyserial websockets
python -m scripts.buzzer_bridge \
    --port /dev/cu.usbserial-XXXX \
    --server wss://quiz.example.com \
    --token <BUZZER_TOKEN aus config.py>
```

Test ohne Hardware:

```bash
python -m scripts.simulate_buzzer --server ws://localhost:8765
```

## Fragen

- CRUD in den Einstellungen
- CSV-Import (`;`-getrennt: `text;a1;a2;a3;a4;correct;skill;category`) oder JSON
- OpenTriviaDB-Seed mit DeepL-Übersetzung: `DEEPL_API_KEY` in `config.py`, dann
  `python -m scripts.seed_questions --amount 50` oder Button im UI

## Deploy (Server)

1. Repo auf Server, venv + requirements installieren
2. `config.py` mit echten Zugangsdaten anlegen, Admin-User via CLI erstellen
3. `deploy/quiz.service` → `/etc/systemd/system/quiz.service`, `systemctl enable --now quiz`
4. `deploy/nginx.example` als nginx-site (WebSocket-Header sind schon drin)

## Tests

```bash
./env/bin/python -m scripts.smoke_test   # Spiellogik (SQLite-Test-DB)
./env/bin/python scripts/ws_test.py      # E2E gegen laufenden Server :8765
```
