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
from app.db import SessionLocal
from app.models import Question

OTDB_URL = "https://opentdb.com/api.php"
DEEPL_URL = "https://api-free.deepl.com/v2/translate"

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
    r = httpx.post(
        DEEPL_URL,
        data={"auth_key": api_key, "target_lang": "DE", "text": texts},
        timeout=60,
    )
    r.raise_for_status()
    return [t["text"] for t in r.json()["translations"]]


def seed(amount=20, category=None, difficulty=None, translate=True):
    results = fetch_opentdb(amount, category, difficulty)
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
    args = p.parse_args()
    n = seed(args.amount, args.category, args.difficulty, not args.no_translate)
    print(f"{n} Fragen importiert")
