from fastapi import HTTPException, Request, status


def check_password(pw: str) -> bool:
    from .config import ADMIN_PASSWORD
    return pw == ADMIN_PASSWORD


def _redirect_to_login():
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/login"},
    )


def require_admin(request: Request):
    if request.session.get("admin"):
        return True
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Nicht angemeldet")


def require_admin_page(request: Request):
    if not request.session.get("admin"):
        _redirect_to_login()
    return True
