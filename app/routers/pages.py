from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..auth import check_login, require_admin_page
from ..config import BASE_DIR
from ..db import get_db

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("/")
def board(request: Request):
    return templates.TemplateResponse(request, "board.html")


@router.get("/board")
def board2(request: Request):
    return templates.TemplateResponse(request, "board.html")


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
async def login(request: Request):
    form = await request.form()
    db = next(get_db())
    try:
        ok = check_login(db, form.get("username", ""), form.get("password", ""))
    finally:
        db.close()
    if ok:
        request.session["admin"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Benutzername oder Passwort falsch"},
        status_code=401,
    )


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/admin", dependencies=[Depends(require_admin_page)])
def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@router.get("/einstellungen", dependencies=[Depends(require_admin_page)])
def settings(request: Request):
    return templates.TemplateResponse(request, "settings.html")
