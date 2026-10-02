"""Question CRUD + bulk import (CSV/JSON) + OpenTriviaDB/DeepL seeding."""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import require_admin
from ..db import get_db
from ..models import Category, CustomQuestion, Question

router = APIRouter(prefix="/api/questions", dependencies=[Depends(require_admin)])


class QuestionBody(BaseModel):
    text: str
    answer1: str
    answer2: str
    answer3: str
    answer4: str
    correct: int
    skill: int = 1
    category: str = ""


def _validate(body: QuestionBody):
    if not 1 <= body.correct <= 4:
        raise HTTPException(400, "correct muss 1-4 sein")
    if not 1 <= body.skill <= 5:
        raise HTTPException(400, "skill muss 1-5 sein")


@router.get("")
def list_questions(q: str | None = None, skill: int | None = None,
                   category: str | None = None, limit: int = 50,
                   offset: int = 0):
    db = next(get_db())
    try:
        query = db.query(Question)
        if q:
            query = query.filter(Question.text.contains(q))
        if skill:
            query = query.filter(Question.skill == skill)
        if category:
            query = query.filter(Question.category == category)
        total = query.count()
        items = (query.order_by(Question.id.desc())
                 .offset(offset).limit(limit).all())
        return {"total": total, "items": [
            x.to_dict(reveal=True) | {"correct": x.correct} for x in items]}
    finally:
        db.close()


@router.get("/reported")
def list_reported():
    """All reported questions from both pools, for the review UI."""
    db = next(get_db())
    try:
        out = []
        for q in db.query(Question).filter(Question.reported == True).all():
            out.append(q.to_dict(reveal=True) | {
                "correct": q.correct, "source": "q",
                "pool": q.category or "Standard-Pool"})
        for q in db.query(CustomQuestion).filter(
                CustomQuestion.reported == True).all():
            cat = db.get(Category, q.category_id)
            out.append(q.to_dict(reveal=True) | {
                "correct": q.correct, "source": "c",
                "pool": f"Kategorie: {cat.name}" if cat else "Kategorie"})
        out.sort(key=lambda x: x["id"], reverse=True)
        return out
    finally:
        db.close()


@router.post("/reported/{src}/{qid}/reactivate")
def reactivate_reported(src: str, qid: int):
    """Clear the reported flag -> question returns to its pool."""
    db = next(get_db())
    try:
        model = CustomQuestion if src == "c" else Question
        q = db.get(model, qid)
        if not q:
            raise HTTPException(404)
        q.reported = False
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/categories")
def categories():
    db = next(get_db())
    try:
        rows = db.query(Question.category).distinct().all()
        return sorted(r[0] for r in rows if r[0])
    finally:
        db.close()


@router.post("")
def add_question(body: QuestionBody):
    _validate(body)
    db = next(get_db())
    try:
        q = Question(**body.model_dump())
        db.add(q)
        db.commit()
        return {"id": q.id}
    finally:
        db.close()


@router.put("/{qid}")
def update_question(qid: int, body: QuestionBody):
    _validate(body)
    db = next(get_db())
    try:
        q = db.get(Question, qid)
        if not q:
            raise HTTPException(404)
        for k, v in body.model_dump().items():
            setattr(q, k, v)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/{qid}")
def delete_question(qid: int):
    db = next(get_db())
    try:
        q = db.get(Question, qid)
        if not q:
            raise HTTPException(404)
        db.delete(q)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/import")
async def import_questions(file: UploadFile):
    """Import JSON: [{"text","answer1"..,"correct","skill","category"}, ...]
    or CSV: text;answer1;answer2;answer3;answer4;correct;skill;category"""
    raw = (await file.read()).decode("utf-8-sig")
    items = []
    name = (file.filename or "").lower()
    if name.endswith(".json") or raw.lstrip().startswith("["):
        items = json.loads(raw)
    else:
        import csv
        import io
        reader = csv.reader(io.StringIO(raw), delimiter=";")
        for row in reader:
            if not row or not row[0].strip() or row[0].startswith("#"):
                continue
            row += [""] * (8 - len(row))
            items.append({
                "text": row[0], "answer1": row[1], "answer2": row[2],
                "answer3": row[3], "answer4": row[4],
                "correct": int(row[5] or 1), "skill": int(row[6] or 1),
                "category": row[7],
            })
    db = next(get_db())
    count = 0
    try:
        for it in items:
            body = QuestionBody(**it)
            _validate(body)
            db.add(Question(**body.model_dump()))
            count += 1
        db.commit()
    finally:
        db.close()
    return {"imported": count}


class SeedBody(BaseModel):
    amount: int = 20
    category: int | None = None       # opentdb category id, None = mixed
    difficulty: str | None = None     # easy|medium|hard, None = mixed
    translate: bool = True


@router.post("/seed")
async def seed_questions(body: SeedBody):
    """Fetch from OpenTriviaDB, translate to German via DeepL, insert."""
    import asyncio
    from scripts.seed_questions import seed
    if not 1 <= body.amount <= 200:
        raise HTTPException(400, "Menge muss 1-200 sein")
    try:
        inserted = await asyncio.get_event_loop().run_in_executor(
            None, seed, body.amount, body.category, body.difficulty, body.translate
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"imported": inserted}
