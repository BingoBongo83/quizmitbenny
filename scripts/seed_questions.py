"""Seed questions from OpenTriviaDB, translated to German via DeepL.

CLI:  python -m scripts.seed_questions --amount 20 --category 9 --difficulty easy
API:  POST /api/questions/seed
"""

import argparse
import html
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import DEEPL_API_KEY
from app.db import Base, SessionLocal, engine
from app.models import Question

OTDB_URL = "https://opentdb.com/api.php"
DEEPL_URL_FREE = "https://api-free.deepl.com/v2/translate"
DEEPL_URL_PRO = "https://api.deepl.com/v2/translate"

SKILL_MAP = {"easy": (1, 2), "medium": (3, 3), "hard": (4, 5)}


def fetch_opentdb(amount=20, category=None, difficulty=None):
    params = {"amount": amount, "type": "multiple"}
    if category:
        params["category"] = category
    if difficulty:
        params["difficulty"] = difficulty
    r = httpx.get(OTDB_URL, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("response_code") != 0:
        raise RuntimeError(f"OpenTriviaDB response_code={data.get('response_code')}")
    return data["results"]


def translate_batch(texts, api_key):
    if not api_key:
        return texts
    api_key = api_key.strip()
    # Free-tier keys end in ':fx' and only work on api-free.deepl.com
    url = DEEPL_URL_FREE if api_key.endswith(":fx") else DEEPL_URL_PRO
    r = httpx.post(
        url,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        data={"target_lang": "DE", "text": texts},
        timeout=60,
    )
    if r.status_code == 403:
        raise RuntimeError(
            "DeepL 403: API-Key ungültig. Prüfe den Key unter "
            "deepl.com -> Account. Hinweis: Free-Keys enden auf ':fx', "
            "Pro-Keys nicht."
        )
    if r.status_code == 456:
        raise RuntimeError("DeepL 456: Zeichenlimit des Kontos erreicht.")
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"DeepL-Fehler: {e}") from e
    return [t["text"] for t in r.json()["translations"]]


def seed(amount=20, category=None, difficulty=None, translate=True):
    # OpenTriviaDB: max 50 per request, ~1 request per 5 s per IP
    results = []
    remaining = amount
    while remaining > 0:
        batch = min(remaining, 50)
        try:
            chunk = fetch_opentdb(batch, category, difficulty)
        except RuntimeError:
            if results:
                break  # partial import is better than none
            raise
        results.extend(chunk)
        remaining -= batch
        if len(chunk) < batch:
            break  # pool exhausted for this category/difficulty
        if remaining > 0:
            time.sleep(5)  # rate limit between OTDB requests
    if translate and not DEEPL_API_KEY:
        raise RuntimeError("DEEPL_API_KEY nicht gesetzt – Übersetzung nicht möglich")

    # collect all texts for one batched DeepL call (q + 4 answers + category)
    flat = []
    for item in results:
        flat.append(html.unescape(item["question"]))
        flat.append(html.unescape(item["correct_answer"]))
        flat.extend(html.unescape(a) for a in item["incorrect_answers"])
        flat.append(item["category"])

    if translate:
        # DeepL batch limit safety: chunk to 50 texts per request
        translated = []
        for i in range(0, len(flat), 50):
            translated.extend(translate_batch(flat[i : i + 50], DEEPL_API_KEY))
            time.sleep(0.5)
        flat = translated

    Base.metadata.create_all(engine)
    db = SessionLocal()
    inserted = 0
    try:
        for i, item in enumerate(results):
            base = i * 6
            text, correct, a2, a3, a4, cat = flat[base : base + 6]
            answers = [correct, a2, a3, a4]
            random.shuffle(answers)
            lo, hi = SKILL_MAP.get(item["difficulty"], (3, 3))
            q = Question(
                text=text,
                answer1=answers[0], answer2=answers[1],
                answer3=answers[2], answer4=answers[3],
                correct=answers.index(correct) + 1,
                skill=random.randint(lo, hi),
                category=cat,
            )
            db.add(q)
            inserted += 1
        db.commit()
    finally:
        db.close()
    return inserted


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--amount", type=int, default=20)
    p.add_argument("--category", type=int, default=None)
    p.add_argument("--difficulty", choices=["easy", "medium", "hard"], default=None)
    p.add_argument("--no-translate", action="store_true")
    p.add_argument("--test-deepl", action="store_true",
                   help="nur DeepL-Key testen, nichts importieren")
    args = p.parse_args()
    if args.test_deepl:
        if not DEEPL_API_KEY:
            print("DEEPL_API_KEY in config.py ist leer")
            sys.exit(1)
        endpoint = "api-free" if DEEPL_API_KEY.strip().endswith(":fx") else "api (Pro)"
        try:
            out = translate_batch(["Hello world"], DEEPL_API_KEY)
            print(f"OK ({endpoint}): 'Hello world' -> '{out[0]}'")
        except RuntimeError as e:
            print(f"Fehler ({endpoint}): {e}")
            sys.exit(1)
        sys.exit(0)
    n = seed(args.amount, args.category, args.difficulty, not args.no_translate)
    print(f"{n} Fragen importiert")
