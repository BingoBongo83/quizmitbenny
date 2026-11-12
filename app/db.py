from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False
else:
    connect_args = {}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BrokenRow:
    """Placeholder for a row that can't be materialized (e.g. invalid
    bytes in a text column from a charset mismatch). Callers check
    `row.broken` to skip it or offer deletion by id."""
    broken = True

    def __init__(self, rid):
        self.id = rid


def fetch_tolerant(db, query, model):
    """query.all(), but rows that fail to decode become BrokenRow
    placeholders instead of failing the whole query."""
    try:
        return query.all()
    except Exception:
        items = []
        for (rid,) in query.with_entities(model.id).all():
            try:
                items.append(db.get(model, rid))
            except Exception:
                items.append(BrokenRow(rid))
        return items


def delete_by_id(db, model, rid):
    """Delete a row without loading it – works even when the row's
    data can't be decoded (BrokenRow case)."""
    db.query(model).filter(model.id == rid).delete()
