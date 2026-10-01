"""Categories + user-authored questions (only played when a round's pool
points at the category)."""

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import require_admin
from ..db import get_db
from ..models import Category, CustomQuestion

router = APIRouter(prefix="/api/categories", dependencies=[Depends(require_admin)])


class CategoryBody(BaseModel):
    name: str


class CustomQuestionBody(BaseModel):
    text: str
    answer1: str
    answer2: str
    answer3: str
    answer4: str
    correct: int
    skill: int = 1


def _validate_cq(body: CustomQuestionBody):
    if not 1 <= body.correct <= 4:
        raise HTTPException(400, "correct muss 1-4 sein")
    if not 1 <= body.skill <= 5:
        raise HTTPException(400, "skill muss 1-5 sein")


# ---------------- categories ----------------

@router.get("")
def list_categories():
    db = next(get_db())
    try:
        return [{
            "id": c.id,
            "name": c.name,
            "count": db.query(CustomQuestion).filter(
                CustomQuestion.category_id == c.id).count(),
        } for c in db.query(Category).order_by(Category.name).all()]
    finally:
        db.close()


@router.post("")
def add_category(body: CategoryBody):
    db = next(get_db())
    try:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name fehlt")
        if db.query(Category).filter(Category.name == name).first():
            raise HTTPException(400, "Kategorie existiert bereits")
        c = Category(name=name)
        db.add(c)
        db.commit()
        return {"id": c.id, "name": c.name}
    finally:
        db.close()


@router.put("/{cid}")
def rename_category(cid: int, body: CategoryBody):
    db = next(get_db())
    try:
        c = db.get(Category, cid)
        if not c:
            raise HTTPException(404)
        c.name = body.name.strip()
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/{cid}")
def delete_category(cid: int):
    """Deletes the category AND all its questions."""
    db = next(get_db())
    try:
        c = db.get(Category, cid)
        if not c:
            raise HTTPException(404)
        db.query(CustomQuestion).filter(
            CustomQuestion.category_id == cid).delete()
        db.delete(c)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---------------- questions within a category ----------------

@router.get("/{cid}/questions")
def list_custom_questions(cid: int):
    db = next(get_db())
    try:
        qs = (db.query(CustomQuestion)
              .filter(CustomQuestion.category_id == cid)
              .order_by(CustomQuestion.id.desc()).all())
        return [q.to_dict(reveal=True) for q in qs]
    finally:
        db.close()


@router.post("/{cid}/questions")
def add_custom_question(cid: int, body: CustomQuestionBody):
    _validate_cq(body)
    db = next(get_db())
    try:
        if not db.get(Category, cid):
            raise HTTPException(404, "Kategorie nicht gefunden")
        q = CustomQuestion(category_id=cid, **body.model_dump())
        db.add(q)
        db.commit()
        return {"id": q.id}
    finally:
        db.close()


@router.put("/{cid}/questions/{qid}")
def update_custom_question(cid: int, qid: int, body: CustomQuestionBody):
    _validate_cq(body)
    db = next(get_db())
    try:
        q = db.get(CustomQuestion, qid)
        if not q or q.category_id != cid:
            raise HTTPException(404)
        for k, v in body.model_dump().items():
            setattr(q, k, v)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.delete("/{cid}/questions/{qid}")
def delete_custom_question(cid: int, qid: int):
    db = next(get_db())
    try:
        q = db.get(CustomQuestion, qid)
        if not q or q.category_id != cid:
            raise HTTPException(404)
        db.delete(q)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.post("/{cid}/import")
async def import_custom_questions(cid: int, file: UploadFile):
    """Same format as the standard import: JSON list or ';' CSV.
    Fields: text;answer1;answer2;answer3;answer4;correct;skill"""
    db = next(get_db())
    try:
        if not db.get(Category, cid):
            raise HTTPException(404, "Kategorie nicht gefunden")
    finally:
        db.close()
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
            row += [""] * (7 - len(row))
            items.append({
                "text": row[0], "answer1": row[1], "answer2": row[2],
                "answer3": row[3], "answer4": row[4],
                "correct": int(row[5] or 1), "skill": int(row[6] or 1),
            })
    db = next(get_db())
    count = 0
    try:
        for it in items:
            body = CustomQuestionBody(**it)
            _validate_cq(body)
            db.add(CustomQuestion(category_id=cid, **body.model_dump()))
            count += 1
        db.commit()
    finally:
        db.close()
    return {"imported": count}
