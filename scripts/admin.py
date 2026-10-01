"""Admin-Benutzer verwalten (Credentials liegen in der Datenbank).

    python -m scripts.admin list
    python -m scripts.admin create <benutzername>
    python -m scripts.admin passwd <benutzername>
    python -m scripts.admin delete <benutzername>
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.models import AdminUser


def _prompt_password(confirm=True):
    while True:
        pw = getpass.getpass("Passwort: ")
        if len(pw) < 6:
            print("Mindestens 6 Zeichen.")
            continue
        if not confirm or getpass.getpass("Wiederholen: ") == pw:
            return pw
        print("Passwörter stimmen nicht überein.")


def cmd_list(db):
    users = db.query(AdminUser).order_by(AdminUser.username).all()
    if not users:
        print("Keine Admin-Benutzer.")
    for u in users:
        print(f"  {u.id}: {u.username}")


def cmd_create(db, username):
    username = username.strip().lower()
    if db.query(AdminUser).filter(AdminUser.username == username).first():
        print(f"Fehler: '{username}' existiert bereits.")
        sys.exit(1)
    db.add(AdminUser(username=username, password_hash=hash_password(_prompt_password())))
    db.commit()
    print(f"Admin '{username}' angelegt.")


def cmd_passwd(db, username):
    user = db.query(AdminUser).filter(
        AdminUser.username == username.strip().lower()).first()
    if not user:
        print(f"Fehler: '{username}' existiert nicht.")
        sys.exit(1)
    user.password_hash = hash_password(_prompt_password())
    db.commit()
    print(f"Passwort für '{username}' geändert.")


def cmd_delete(db, username):
    user = db.query(AdminUser).filter(
        AdminUser.username == username.strip().lower()).first()
    if not user:
        print(f"Fehler: '{username}' existiert nicht.")
        sys.exit(1)
    if db.query(AdminUser).count() == 1:
        print("Fehler: letzter Admin kann nicht gelöscht werden.")
        sys.exit(1)
    db.delete(user)
    db.commit()
    print(f"Admin '{username}' gelöscht.")


def main():
    p = argparse.ArgumentParser(description="Admin-Benutzer verwalten")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("create", "passwd", "delete"):
        sp = sub.add_parser(name)
        sp.add_argument("username")
    args = p.parse_args()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if args.cmd == "list":
            cmd_list(db)
        elif args.cmd == "create":
            cmd_create(db, args.username)
        elif args.cmd == "passwd":
            cmd_passwd(db, args.username)
        elif args.cmd == "delete":
            cmd_delete(db, args.username)
    finally:
        db.close()


if __name__ == "__main__":
    main()
