"""Seed questions from OpenTriviaDB, translated to German via Azure OpenAI.

Falls back to OpenAI, then DeepL, depending on which keys are configured.

CLI:  python -m scripts.seed_questions --amount 20 --category 9 --difficulty easy
API:  POST /api/questions/seed
"""

import argparse
import html
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_MODEL,
    DEEPL_API_KEY,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from app.db import Base, SessionLocal, engine
from app.models import Question

OTDB_URL = "https://opentdb.com/api.php"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
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


def translator():
    """Active translation provider: 'azure' | 'openai' | 'deepl' | None."""
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_MODEL:
        return "azure"
    if OPENAI_API_KEY:
        return "openai"
    if DEEPL_API_KEY:
        return "deepl"
    return None


def _chat_translate(texts, provider, url, headers, params=None, model=None):
    """Shared chat-completions call for OpenAI and Azure OpenAI."""
    body = {
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Du übersetzt Quizfragen und Antworten von Englisch "
                    "nach Deutsch. Die Eingabe ist ein JSON-Array von "
                    "Strings. Antworte ausschließlich mit einem JSON-"
                    'Objekt {"translations": [...]} mit exakt gleich '
                    "vielen Einträgen in gleicher Reihenfolge. Übersetze "
                    "natürlich und knapp; Eigennamen, Marken und "
                    "Songtitel bleiben unverändert."
                ),
            },
            {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
        ],
    }
    if model:  # Azure routes by deployment in the URL, not by body
        body["model"] = model
    r = httpx.post(url, params=params, headers=headers, json=body, timeout=120)
    if r.status_code == 401:
        raise RuntimeError(f"{provider} 401: API-Key ungültig.")
    if r.status_code == 429:
        raise RuntimeError(f"{provider} 429: Rate-Limit oder Kontingent erreicht.")
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"{provider}-Fehler: {e}") from e
    content = r.json()["choices"][0]["message"]["content"]
    try:
        out = json.loads(content).get("translations")
    except ValueError:
        out = None
    if not isinstance(out, list) or len(out) != len(texts):
        raise RuntimeError(
            f"{provider}-Antwort unbrauchbar (erwartet {len(texts)} Übersetzungen)"
        )
    return [str(t) for t in out]


def azure_translate(texts):
    url = (
        f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{AZURE_OPENAI_MODEL}"
        "/chat/completions"
    )
    return _chat_translate(
        texts,
        "Azure OpenAI",
        url,
        {"api-key": AZURE_OPENAI_API_KEY.strip()},
        params={"api-version": AZURE_OPENAI_API_VERSION},
    )


def openai_translate(texts):
    return _chat_translate(
        texts,
        "OpenAI",
        OPENAI_URL,
        {"Authorization": f"Bearer {OPENAI_API_KEY.strip()}"},
        model=OPENAI_MODEL,
    )


def deepl_translate(texts):
    api_key = DEEPL_API_KEY.strip()
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


def translate_batch(texts):
    prov = translator()
    if prov == "azure":
        return azure_translate(texts)
    if prov == "openai":
        return openai_translate(texts)
    if prov == "deepl":
        return deepl_translate(texts)
    return texts


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
    if translate and not translator():
        raise RuntimeError(
            "Weder AZURE_OPENAI_*/OPENAI_API_KEY noch DEEPL_API_KEY gesetzt – "
            "Übersetzung nicht möglich"
        )

    # collect all texts for one batched call (q + 4 answers + category)
    flat = []
    for item in results:
        flat.append(html.unescape(item["question"]))
        flat.append(html.unescape(item["correct_answer"]))
        flat.extend(html.unescape(a) for a in item["incorrect_answers"])
        flat.append(item["category"])

    if translate:
        # batch limit safety: chunk to 50 texts per request
        translated = []
        for i in range(0, len(flat), 50):
            translated.extend(translate_batch(flat[i : i + 50]))
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
    p.add_argument("--test-translate", "--test-deepl", dest="test_translate",
                   action="store_true",
                   help="nur Übersetzungs-Key testen, nichts importieren")
    args = p.parse_args()
    if args.test_translate:
        prov = translator()
        if not prov:
            print("Kein Übersetzungs-Key gesetzt "
                  "(AZURE_OPENAI_*/OPENAI_API_KEY/DEEPL_API_KEY)")
            sys.exit(1)
        try:
            out = translate_batch(["Hello world"])
            print(f"OK ({prov}): 'Hello world' -> '{out[0]}'")
        except RuntimeError as e:
            print(f"Fehler ({prov}): {e}")
            sys.exit(1)
        sys.exit(0)
    n = seed(args.amount, args.category, args.difficulty, not args.no_translate)
    print(f"{n} Fragen importiert")
