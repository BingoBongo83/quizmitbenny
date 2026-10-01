import hashlib
import hmac
import os

from fastapi import HTTPException, Request, status

from .models import AdminUser

_ITERATIONS = 260_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def check_login(db, username: str, password: str) -> bool:
    user = (
        db.query(AdminUser)
        .filter(AdminUser.username == username.strip().lower())
        .first()
    )
    return bool(user and verify_password(password, user.password_hash))


def require_admin(request: Request):
    if request.session.get("admin"):
        return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Nicht angemeldet")


def require_admin_page(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return True
