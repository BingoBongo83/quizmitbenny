"""Scan question tables for rows that can't be materialized or contain
invalid data (e.g. non-UTF8 bytes, out-of-range correct/skill).

Such rows previously broke whole list pages (API 500 -> pager dead).

Usage:  env/bin/python -m scripts.check_questions
"""

import sys

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from app.db import get_db  # noqa: E402
from app.models import CustomQuestion, Question  # noqa: E402


def scan(db, model, name):
    broken, invalid = [], []
    for (rid,) in db.query(model.id).all():
        try:
            q = db.get(model, rid)
        except Exception as e:  # noqa: BLE001 – any decode/driver error
            broken.append((rid, str(e)[:80]))
            db.rollback()
            continue
        problems = []
        if not 1 <= (q.correct or 0) <= 4:
            problems.append(f"correct={q.correct}")
        if not 1 <= (q.skill or 0) <= 5:
            problems.append(f"skill={q.skill}")
        if not (q.text or "").strip():
            problems.append("leerer Fragetext")
        if not all((a or "").strip() for a in q.answers()):
            problems.append("leere Antwort")
        if problems:
            invalid.append((rid, ", ".join(problems)))
    print(f"{name}: {len(broken)} kaputt, {len(invalid)} ungültig")
    for rid, e in broken:
        print(f"  KAPUTT id={rid}: {e}")
    for rid, p in invalid:
        print(f"  ungültig id={rid}: {p}")
    return len(broken) + len(invalid)


def main():
    db = next(get_db())
    try:
        bad = scan(db, Question, "questions")
        bad += scan(db, CustomQuestion, "custom_questions")
    finally:
        db.close()
    print("OK – alles sauber" if not bad else f"{bad} Problemzeilen gefunden")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
