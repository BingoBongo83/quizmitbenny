"""Question CRUD + bulk import (CSV/JSON) + OpenTriviaDB seeding (OpenAI/DeepL)."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..auth import require_admin
from ..db import delete_by_id, fetch_tolerant, get_db
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
    comment: str = ""


def _validate(body: QuestionBody):
    if not 1 <= body.correct <= 4:
        raise HTTPException(400, "correct muss 1-4 sein")
    if not 1 <= body.skill <= 5:
        raise HTTPException(400, "skill muss 1-5 sein")


def _question_sane(x):
    """Row materialized AND its text fields are real strings – anything
    else (decode leftovers, blobs) would break JSON serialization."""
    return (not getattr(x, "broken", False)
            and isinstance(getattr(x, "text", None), str)
            and all(isinstance(getattr(x, f"answer{i}", ""), str)
                    for i in range(1, 5)))


def _question_dict(x):
    """to_dict incl. correct; corrupt rows render as a deletable
    placeholder instead of failing the whole list."""
    if not _question_sane(x):
        return {"id": x.id,
                "text": "⚠ Fehlerhafte Frage – bitte löschen",
                "answers": ["", "", "", ""], "skill": 0,
                "category": "⚠ defekt", "used": False,
                "reported": False, "correct": 1, "category_id": None,
                "comment": "", "broken": True}
    return x.to_dict(reveal=True) | {"correct": x.correct}


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
        items = fetch_tolerant(
            db, query.order_by(Question.id.desc())
            .offset(offset).limit(limit), Question)
        return {"total": total, "items": [_question_dict(x) for x in items]}
    finally:
        db.close()


@router.get("/reported")
def list_reported():
    """All reported questions from both pools, for the review UI."""
    db = next(get_db())
    try:
        out = []
        for q in fetch_tolerant(
                db, db.query(Question).filter(Question.reported == True),
                Question):
            out.append(_question_dict(q) | {
                "source": "q",
                "pool": getattr(q, "category", None) or "Standard-Pool"})
        for q in fetch_tolerant(
                db, db.query(CustomQuestion).filter(
                    CustomQuestion.reported == True), CustomQuestion):
            cat = db.get(Category, q.category_id) if q.category_id else None
            out.append(_question_dict(q) | {
                "source": "c",
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
        try:
            q = db.get(model, qid)
        except Exception:
            # corrupt row – clear the flag without materializing it
            db.query(model).filter(model.id == qid).update(
                {"reported": False}, synchronize_session=False)
            db.commit()
            return {"ok": True}
        if not q:
            raise HTTPException(404)
        q.reported = False
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/export")
def export_questions(format: str = "json", skill: int | None = None,
                     cats: list[str] = Query(default=[])):
    """Export questions as JSON or CSV.

    cats selects subsets: 'std:<name>' for standard-pool categories,
    'cat:<id>' for custom categories. Empty = everything.
    Output rows: {text, answer1-4, correct, skill, category}
    (custom questions get their category name as 'category').
    """
    db = next(get_db())
    try:
        std_cats = {c[4:] for c in cats if c.startswith("std:")}
        cat_ids = {int(c[4:]) for c in cats
                   if c.startswith("cat:") and c[4:].isdigit()}
        items = []
        if not cats or std_cats:
            q = db.query(Question)
            if skill:
                q = q.filter(Question.skill == skill)
            if std_cats:
                q = q.filter(Question.category.in_(std_cats))
            items += [
                {"text": x.text, "answer1": x.answer1, "answer2": x.answer2,
                 "answer3": x.answer3, "answer4": x.answer4,
                 "correct": x.correct, "skill": x.skill,
                 "category": x.category or "",
                 "comment": x.comment or ""}
                for x in fetch_tolerant(
                    db, q.order_by(Question.id), Question)
                if not getattr(x, "broken", False)
            ]
        if not cats or cat_ids:
            cat_names = {c.id: c.name for c in db.query(Category).all()}
            q = db.query(CustomQuestion)
            if skill:
                q = q.filter(CustomQuestion.skill == skill)
            if cat_ids:
                q = q.filter(CustomQuestion.category_id.in_(cat_ids))
            items += [
                {"text": x.text, "answer1": x.answer1, "answer2": x.answer2,
                 "answer3": x.answer3, "answer4": x.answer4,
                 "correct": x.correct, "skill": x.skill,
                 "category": cat_names.get(x.category_id, ""),
                 "comment": x.comment or ""}
                for x in fetch_tolerant(
                    db, q.order_by(CustomQuestion.id), CustomQuestion)
                if not getattr(x, "broken", False)
            ]
        if format == "csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            for it in items:
                w.writerow([it["text"], it["answer1"], it["answer2"],
                            it["answer3"], it["answer4"], it["correct"],
                            it["skill"], it["category"], it["comment"]])
            return Response(
                buf.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition":
                         'attachment; filename="questions_export.csv"'},
            )
        return Response(
            json.dumps(items, ensure_ascii=False, indent=1),
            media_type="application/json",
            headers={"Content-Disposition":
                     'attachment; filename="questions_export.json"'},
        )
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
        try:
            q = db.get(Question, qid)
        except Exception:
            q = None
            broken = True
        else:
            broken = False
        if not q:
            if not broken:
                raise HTTPException(404)
            delete_by_id(db, Question, qid)
        else:
            db.delete(q)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


_XLSX_HEADERS = {"frage", "text", "question"}


def _row_to_item(row):
    """One import row -> question dict.
    Columns: text | answer1..4 | correct (1-4) | skill (1-5) | category
    | comment (optional, moderator trivia)"""
    row = ["" if c is None else str(c).strip() for c in row]
    row += [""] * (9 - len(row))
    try:
        return {
            "text": row[0], "answer1": row[1], "answer2": row[2],
            "answer3": row[3], "answer4": row[4],
            "correct": int(float(row[5] or 1)),
            "skill": int(float(row[6] or 1)),
            "category": row[7],
            "comment": row[8],
        }
    except ValueError:
        raise HTTPException(
            400, f"Zeile ungültig: {';'.join(row)[:80]}")


def _items_from_xlsx(raw: bytes):
    """Parse an .xlsx upload with the same column layout as the CSV import."""
    import io
    import openpyxl
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(400, "Excel-Datei konnte nicht gelesen werden")
    try:
        rows = list(wb.active.iter_rows(values_only=True))
    finally:
        wb.close()
    items = []
    for row in rows:
        first = str(row[0]).strip() if row and row[0] is not None else ""
        if not first or first.lower() in _XLSX_HEADERS:
            continue  # blank rows + header row
        ncols = sum(1 for c in row if c is not None and str(c).strip())
        items.append((_row_to_item(row), ncols))
    return items


@router.get("/template.xlsx")
def download_template():
    """Sample .xlsx for the question import (header + example rows)."""
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fragen"
    ws.append(["Frage", "Antwort 1", "Antwort 2", "Antwort 3", "Antwort 4",
               "Richtig (1-4)", "Skill (1-5)", "Kategorie", "Kommentar"])
    ws.append(["Was ist die Hauptstadt von Frankreich?",
               "Paris", "London", "Berlin", "Rom", 1, 1, "Geografie",
               "Paris liegt an der Seine und ist seit 508 n. Chr. "
               "Hauptstadt."])
    ws.append(["Wie viele Beine hat eine Spinne?",
               "6", "8", "10", "12", 2, 2, "Tiere",
               "Spinnen sind keine Insekten – Insekten haben 6 Beine."])
    ws.append(["Wer sang 'Imagine'?",
               "Elvis Presley", "John Lennon", "Freddie Mercury",
               "Michael Jackson", 2, 3, "Musik",
               "'Imagine' erschien 1971 auf Lennons Soloalbum."])
    for col, w in zip("ABCDEFGHI", (45, 18, 18, 18, 18, 12, 11, 15, 45)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-"
                   "officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 'attachment; filename="fragen_vorlage.xlsx"'},
    )


@router.post("/import")
async def import_questions(file: UploadFile):
    """Import JSON: [{"text","answer1"..,"correct","skill","category"}, ...],
    ';' CSV, or .xlsx with the same column layout."""
    raw_bytes = await file.read()
    items = []
    name = (file.filename or "").lower()
    if name.endswith(".xlsx") or raw_bytes[:4] == b"PK\x03\x04":
        items = [it for it, _ in _items_from_xlsx(raw_bytes)]
    else:
        raw = raw_bytes.decode("utf-8-sig")
        if name.endswith(".json") or raw.lstrip().startswith("["):
            try:
                items = json.loads(raw)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    400, f"JSON ungültig: {e.msg} – Zeile {e.lineno}, "
                         f"Spalte {e.colno}")
            if not isinstance(items, list):
                raise HTTPException(400, "JSON muss ein Array von Fragen sein")
        else:
            import csv
            import io
            reader = csv.reader(io.StringIO(raw), delimiter=";")
            for row in reader:
                if not row or not row[0].strip() or row[0].startswith("#"):
                    continue
                items.append(_row_to_item(row))
    db = next(get_db())
    count = 0
    try:
        for i, it in enumerate(items, 1):
            try:
                body = QuestionBody(**it)
                _validate(body)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, f"Eintrag {i} ungültig: {e}")
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
    """Fetch from OpenTriviaDB, translate to German (OpenAI/DeepL), insert."""
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
