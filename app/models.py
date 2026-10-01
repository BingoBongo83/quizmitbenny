import json

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String, Text

from .db import Base


class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    active = Column(Boolean, default=True)


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    answer1 = Column(Text, nullable=False)
    answer2 = Column(Text, nullable=False)
    answer3 = Column(Text, nullable=False)
    answer4 = Column(Text, nullable=False)
    correct = Column(Integer, nullable=False)  # 1-4
    skill = Column(Integer, nullable=False, default=1)  # 1-5
    category = Column(String(255), default="")
    used = Column(Boolean, default=False)
    used_round_id = Column(Integer, nullable=True)
    reported = Column(Boolean, default=False)  # flagged by moderator, excluded from pools

    def answers(self):
        return [self.answer1, self.answer2, self.answer3, self.answer4]

    def to_dict(self, reveal=False):
        d = {
            "id": self.id,
            "text": self.text,
            "answers": self.answers(),
            "skill": self.skill,
            "category": self.category,
            "used": self.used,
            "reported": self.reported,
        }
        if reveal:
            d["correct"] = self.correct
        return d


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)


class CustomQuestion(Base):
    """User-authored questions bound to a Category. Only played when the
    round's question_pool points at that category – never in the standard pool."""

    __tablename__ = "custom_questions"

    id = Column(Integer, primary_key=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    text = Column(Text, nullable=False)
    answer1 = Column(Text, nullable=False)
    answer2 = Column(Text, nullable=False)
    answer3 = Column(Text, nullable=False)
    answer4 = Column(Text, nullable=False)
    correct = Column(Integer, nullable=False)  # 1-4
    skill = Column(Integer, nullable=False, default=1)  # 1-5
    used = Column(Boolean, default=False)
    used_round_id = Column(Integer, nullable=True)
    reported = Column(Boolean, default=False)  # flagged by moderator, excluded from pools

    def answers(self):
        return [self.answer1, self.answer2, self.answer3, self.answer4]

    def to_dict(self, reveal=False):
        d = {
            "id": self.id,
            "text": self.text,
            "answers": self.answers(),
            "skill": self.skill,
            "category_id": self.category_id,
            "used": self.used,
            "reported": self.reported,
        }
        if reveal:
            d["correct"] = self.correct
        return d


class Round(Base):
    """type: preround | playoff_presemi | semifinal | playoff_prefinal | final"""

    __tablename__ = "rounds"

    id = Column(Integer, primary_key=True)
    number = Column(Integer, nullable=False)  # ordering within the game
    type = Column(String(32), nullable=False)
    status = Column(String(32), default="pending")  # pending|active|finished
    skill_levels = Column(JSON, default=lambda: [1, 2, 3, 4, 5])
    question_pool = Column(String(64), default="standard")  # 'standard'|'cat:<id>'


class RoundPlayer(Base):
    __tablename__ = "round_players"

    id = Column(Integer, primary_key=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    slot = Column(Integer, nullable=False)  # buzzer slot 1-5
    score = Column(Integer, default=0)
    place = Column(Integer, nullable=True)
    qualified = Column(String(32), nullable=True)  # 'direct'|'playoff'|'out'|None


class ConfigKV(Base):
    __tablename__ = "config"

    name = Column(String(255), primary_key=True)
    value = Column(Text)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)


DEFAULT_SETTINGS = {
    "players_per_round": 4,
    "num_prerounds": 3,
    "points_correct": 2,
    "points_wrong_self": 0,
    "points_wrong_others": 2,
    "block_on_wrong": True,
    "sound_target": "board",  # board | admin | both
    # seat (slot) colors — bound to the physical buzzer position, not players
    "seat_colors": ["#2e6bff", "#ffd23c", "#ff8c1a", "#ff5ec4", "#2ee56f"],
    "joker_fifty": True,
    "joker_double": True,
    "joker_audience": True,
    # moderator lock-in ("eingeloggt") before judging; audience joker always locks
    "answer_lockin": True,
    # per-round question cap: moderator must finish the round when reached
    "max_questions_enabled": False,
    "max_questions": 20,
    "skills_preround": [1, 2, 3],
    "skills_playoff": [3, 4],
    "skills_semifinal": [3, 4, 5],
    "skills_final": [4, 5],
}


def get_setting(db, key, default=None):
    if default is None:
        default = DEFAULT_SETTINGS.get(key)
    row = db.query(ConfigKV).filter(ConfigKV.name == f"setting:{key}").first()
    if row is None or row.value is None:
        return default
    try:
        return json.loads(row.value)
    except (ValueError, TypeError):
        return default


def set_setting(db, key, value):
    name = f"setting:{key}"
    row = db.query(ConfigKV).filter(ConfigKV.name == name).first()
    if row is None:
        row = ConfigKV(name=name)
        db.add(row)
    row.value = json.dumps(value)
    db.commit()


def get_all_settings(db):
    out = dict(DEFAULT_SETTINGS)
    rows = db.query(ConfigKV).filter(ConfigKV.name.like("setting:%")).all()
    for row in rows:
        try:
            out[row.name[len("setting:"):]] = json.loads(row.value)
        except (ValueError, TypeError):
            pass
    return out
