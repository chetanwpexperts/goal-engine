"""Free accounts: signup, email code, login with limits, sessions, password reset.

Chats belong to a visitor id. An account owns one visitor id (the guest's own, adopted at signup), so a logged-in
person sees the same goals on every device and the rest of the app needs no change.
Passwords: scrypt (standard library). Codes and session tokens: only their hashes are stored.
"""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import AuthAttempt, EmailCode, User, UserSession, utcnow  # noqa: F401 - User used by main

log = logging.getLogger(__name__)
router = APIRouter()

SESSION_COOKIE = "ge_sess"
SESSION_DAYS = 30
SESSION_TOUCH = timedelta(minutes=5)          # last_seen is written at most this often
CODE_TTL = timedelta(minutes=10)
CODE_TRIES = 5                                # wrong guesses per code, then it's dead
LOCK_AFTER = 5                                # wrong passwords in a row lock the account...
LOCK_FOR = timedelta(minutes=15)              # ...for this long
# (key prefix, max attempts, window)
LIMITS = {
    "login-ip": (20, timedelta(minutes=15)),
    "signup-ip": (5, timedelta(hours=1)),
    "code-send-user": (5, timedelta(hours=1)),
    "code-send-ip": (10, timedelta(hours=1)),
    "code-check-user": (20, timedelta(hours=1)),
    "forgot-ip": (5, timedelta(hours=1)),
    "upload-ip": (30, timedelta(hours=1)),       # a fresh browser cookie doesn't give fresh uploads
}
ATTEMPT_KEEP = timedelta(days=2)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{2,29}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,19}$")

# scrypt: 16 MB per hash, about 50 ms. Format: scrypt$n$r$p$salt$hash
_N, _R, _P = 2 ** 14, 8, 1


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400, field: str = ""):
        super().__init__(message)
        self.status, self.field = status, field


# ---------------------------------------------------------------- passwords, codes, tokens
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY = None


def _dummy_check(password: str) -> None:
    """Same work for an unknown user, so response time doesn't reveal who has an account."""
    global _DUMMY
    _DUMMY = _DUMMY or hash_password(secrets.token_hex(8))
    check_password(password, _DUMMY)


def _secret() -> bytes:
    key = os.getenv("APP_SECRET", "")
    if len(key) < 16:
        raise AccountError("Accounts aren't set up on the server yet.", 503)
    return key.encode()


def _code_hash(code_id_seed: str, code: str) -> str:
    return hmac.new(_secret(), f"{code_id_seed}:{code}".encode(), "sha256").hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------- limits
def _limit(db: Session, kind: str, who: str) -> None:
    """Count this attempt; refuse when over the limit for its window."""
    most, window = LIMITS[kind]
    key = f"{kind}:{who}"[:120]
    now = utcnow()
    recent = db.scalar(select(func.count()).select_from(AuthAttempt).where(AuthAttempt.key == key, AuthAttempt.at > now - window))
    if recent >= most:
        mins = max(1, int(window.total_seconds() // 60))
        raise AccountError(f"Too many tries. Please wait {mins} minutes and try again." if mins < 60
                           else "Too many tries. Please try again in an hour.", 429)
    db.add(AuthAttempt(key=key, at=now))
    db.commit()


def maintain(db: Session) -> dict:
    now = utcnow()
    done = {
        "auth_attempts": db.execute(delete(AuthAttempt).where(AuthAttempt.at < now - ATTEMPT_KEEP)).rowcount,
        "email_codes": db.execute(delete(EmailCode).where(EmailCode.expires_at < now - timedelta(days=1))).rowcount,
        "sessions": db.execute(delete(UserSession).where(UserSession.expires_at < now)).rowcount,
    }
    db.commit()
    return done


# ---------------------------------------------------------------- email
_mailer = None


def mailer():
    global _mailer
    if _mailer is None:
        from app.agents.mailer import SmtpMailer

        _mailer = SmtpMailer()
    return _mailer


def send_code(db: Session, user: User, purpose: str, ip: str) -> None:
    _limit(db, "code-send-user", str(user.id))
    _limit(db, "code-send-ip", ip)
    m = mailer()
    if not getattr(m, "configured", True) and os.getenv("AUTH_DEV_CODES") != "1":
        raise AccountError("We can't send email right now. Please try again later.", 503)
    # one live code per purpose: a new one replaces the old
    for old in db.scalars(select(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose, EmailCode.used.is_(False))):
        old.used = True
    code = f"{secrets.randbelow(10 ** 6):06d}"
    seed = secrets.token_hex(8)
    row = EmailCode(user_id=user.id, purpose=purpose, code_hash=f"{seed}${_code_hash(seed, code)}", expires_at=utcnow() + CODE_TTL)
    db.add(row)
    db.commit()
    what = {"verify": "confirm your email", "reset": "set a new password", "database": "open your database area"}[purpose]
    body = (f"Hi {user.full_name.split()[0] if user.full_name else ''},\n\n"
            f"Your Goal Engine code is {code}\n\n"
            f"Use it to {what}. It works for 10 minutes, once.\n"
            "If you didn't ask for this, you can ignore this email. Nobody can get in without the code.\n\n"
            "Goal Engine")
    if not getattr(m, "configured", True):
        log.warning("AUTH_DEV_CODES: code for %s (%s): %s", user.email, purpose, code)
        return
    try:
        m.send(user.email, f"Your Goal Engine code: {code}", body)
    except Exception:  # noqa: BLE001 - never show SMTP details to the visitor
        log.exception("Sending the %s code failed", purpose)
        raise AccountError("We couldn't send the email. Please try again in a minute.", 503)


def check_code(db: Session, user: User, purpose: str, code: str) -> None:
    _limit(db, "code-check-user", str(user.id))
    code = re.sub(r"\D", "", code or "")
    row = db.scalar(select(EmailCode).where(EmailCode.user_id == user.id, EmailCode.purpose == purpose, EmailCode.used.is_(False))
                    .order_by(EmailCode.id.desc()))
    if row is None or row.expires_at < utcnow() or row.attempts >= CODE_TRIES:
        raise AccountError("This code has expired. Ask for a new one.", field="code")
    seed, want = row.code_hash.split("$", 1)
    if len(code) != 6 or not hmac.compare_digest(_code_hash(seed, code), want):
        row.attempts += 1
        if row.attempts >= CODE_TRIES:
            row.used = True
        db.commit()
        left = CODE_TRIES - row.attempts
        raise AccountError("That code isn't right." + (f" {left} tries left." if left > 0 else " Ask for a new one."), field="code")
    row.used = True
    db.commit()


# ---------------------------------------------------------------- sessions
def client_ip(request: Request) -> str:
    # the address nginx saw (the right-most forwarded one); the left-most part can be faked by the client
    fwd = [x.strip() for x in (request.headers.get("x-forwarded-for") or "").split(",") if x.strip()]
    return (fwd[-1] if fwd else (request.client.host if request.client else ""))[:64]


def _start_session(db: Session, user: User, request: Request, response: Response) -> None:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(UserSession(user_id=user.id, token_hash=_token_hash(token), expires_at=now + timedelta(days=SESSION_DAYS),
                       ip=client_ip(request)))
    user.last_login_at = now
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=get_settings().cookie_secure)


def session_for(db: Session, token: str) -> UserSession | None:
    if not token or len(token) > 100:
        return None
    s = db.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if s is None or s.expires_at < utcnow():
        return None
    if utcnow() - s.last_seen_at > SESSION_TOUCH:
        s.last_seen_at = utcnow()
        db.commit()
    return s


def resolve(request: Request) -> None:
    """Middleware helper: request.state.user / .user_session for a valid session cookie (else None)."""
    request.state.user = None
    request.state.user_session = None
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return
    from app.db import get_engine
    from sqlalchemy.orm import Session as S

    with S(get_engine(), expire_on_commit=False) as db:
        s = session_for(db, token)
        if s is not None:
            user = db.get(User, s.user_id)
            if user is not None:
                request.state.user, request.state.user_session = user, s


def current_user(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def as_dict(user: User) -> dict:
    return {"full_name": user.full_name, "username": user.username, "email": user.email, "phone": user.phone,
            "location": user.location, "why": user.why, "email_verified": bool(user.email_verified)}


def _err(exc: AccountError) -> JSONResponse:
    return JSONResponse({"error": str(exc), "field": exc.field}, status_code=exc.status)


# ---------------------------------------------------------------- endpoints
class SignupIn(BaseModel):
    full_name: str = ""
    phone: str = ""
    email: str = ""
    location: str = ""
    username: str = ""
    password: str = ""
    why: str = ""
    website: str = ""          # honeypot: people never see it


class LoginIn(BaseModel):
    login: str = ""            # email or username
    password: str = ""


class CodeIn(BaseModel):
    code: str = ""


class ForgotIn(BaseModel):
    email: str = ""


class ResetIn(BaseModel):
    email: str = ""
    code: str = ""
    password: str = ""


def _clean(body: SignupIn) -> dict:
    d = {
        "full_name": " ".join(body.full_name.split())[:120],
        "phone": body.phone.strip(),
        "email": body.email.strip().lower(),
        "location": " ".join(body.location.split())[:120],
        "username": body.username.strip().lower(),
        "why": body.why.strip()[:2000],
    }
    if len(d["full_name"]) < 2:
        raise AccountError("Please enter your full name.", field="full_name")
    if not PHONE_RE.match(d["phone"]) or not 7 <= len(re.sub(r"\D", "", d["phone"])) <= 15:
        raise AccountError("Please enter a valid phone number, with country code if outside India.", field="phone")
    if not EMAIL_RE.match(d["email"]):
        raise AccountError("Please enter a valid email address.", field="email")
    if len(d["location"]) < 2:
        raise AccountError("Please enter your city or country.", field="location")
    if not USERNAME_RE.match(d["username"]):
        raise AccountError("Username: 3 to 30 letters, numbers, dots or underscores, starting with a letter or number.", field="username")
    if len(d["why"].split()) < 5:
        raise AccountError("Please tell us a little about your work or business (a sentence or two).", field="why")
    _check_new_password(body.password, d["username"], d["email"])
    return d


def _check_new_password(password: str, username: str, email: str) -> None:
    if not 10 <= len(password) <= 128:
        raise AccountError("Password: at least 10 characters.", field="password")
    low = password.lower()
    if low in (username, email, email.split("@")[0]) or len(set(password)) < 4:
        raise AccountError("Please choose a password that's harder to guess.", field="password")


@router.post("/api/account/signup")
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if current_user(request):
        return _err(AccountError("You're already logged in."))
    try:
        if body.website:
            raise AccountError("Please try again.")
        d = _clean(body)                  # a typo in the form doesn't use up the limit
        _limit(db, "signup-ip", client_ip(request))
        if db.scalar(select(User.id).where(User.email == d["email"])):
            raise AccountError("An account with this email already exists. Log in instead.", field="email")
        if db.scalar(select(User.id).where(User.username == d["username"])):
            raise AccountError("This username is taken. Try another.", field="username")
        visitor = getattr(request.state, "visitor_id", "") or ""
        # the guest's chats come with them, unless this browser's id already belongs to an account
        if not visitor or db.scalar(select(User.id).where(User.visitor_id == visitor)):
            visitor = secrets.token_urlsafe(24)
        user = User(**d, password_hash=hash_password(body.password), visitor_id=visitor)
        db.add(user)
        db.commit()
        _start_session(db, user, request, response)
        try:
            send_code(db, user, "verify", client_ip(request))
            sent = True
        except AccountError:
            sent = False          # the account exists; they can ask for the code again
        return {"ok": True, "user": as_dict(user), "code_sent": sent}
    except AccountError as exc:
        return _err(exc)


@router.post("/api/account/verify")
def verify(body: CodeIn, request: Request, db: Session = Depends(get_db)):
    user = current_user(request)
    if user is None:
        return _err(AccountError("Please log in first.", 401))
    user = db.get(User, user.id)
    try:
        if not user.email_verified:
            check_code(db, user, "verify", body.code)
            user.email_verified = True
            db.commit()
        return {"ok": True, "user": as_dict(user)}
    except AccountError as exc:
        return _err(exc)


@router.post("/api/account/resend")
def resend(request: Request, db: Session = Depends(get_db)):
    user = current_user(request)
    if user is None:
        return _err(AccountError("Please log in first.", 401))
    user = db.get(User, user.id)
    if user.email_verified:
        return {"ok": True, "already": True}
    try:
        send_code(db, user, "verify", client_ip(request))
        return {"ok": True}
    except AccountError as exc:
        return _err(exc)


WRONG_LOGIN = "Wrong email/username or password."


@router.post("/api/account/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    try:
        _limit(db, "login-ip", client_ip(request))
        who = body.login.strip().lower()
        user = db.scalar(select(User).where(or_(User.email == who, User.username == who))) if who else None
        if user is None:
            _dummy_check(body.password)
            raise AccountError(WRONG_LOGIN, 401)
        now = utcnow()
        if user.locked_until and user.locked_until > now:
            mins = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
            raise AccountError(f"Too many wrong passwords. This account is locked for {mins} more minutes, "
                               "or use 'Forgot password' to get in now.", 429)
        if not check_password(body.password, user.password_hash):
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= LOCK_AFTER:
                user.locked_until, user.failed_logins = now + LOCK_FOR, 0
            db.commit()
            raise AccountError(WRONG_LOGIN, 401)
        user.failed_logins, user.locked_until = 0, None
        db.commit()
        _start_session(db, user, request, response)
        return {"ok": True, "user": as_dict(user)}
    except AccountError as exc:
        return _err(exc)


def _new_guest_cookie(response: Response) -> None:
    # after logout this browser is a new guest: the account's chats must not stay visible here
    from app.main import COOKIE

    response.set_cookie(COOKIE, secrets.token_urlsafe(24), max_age=60 * 60 * 24 * 180, httponly=True, samesite="lax",
                        secure=get_settings().cookie_secure)


@router.post("/api/account/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    s = getattr(request.state, "user_session", None)
    if s is not None:
        from app import dbarea

        sess = db.get(UserSession, s.id)
        if sess is not None:
            dbarea.lock(db, sess)                 # typed database passwords go with the session
        db.execute(delete(UserSession).where(UserSession.id == s.id))
        db.commit()
    response.delete_cookie(SESSION_COOKIE)
    _new_guest_cookie(response)
    return {"ok": True}


@router.get("/api/account/me")
def me(request: Request):
    from app import dbarea

    user = current_user(request)
    return {"user": as_dict(user) if user else None, "db_connect": dbarea.connect_open()}


@router.post("/api/account/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    try:
        _limit(db, "forgot-ip", client_ip(request))
    except AccountError as exc:
        return _err(exc)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is not None:
        try:
            send_code(db, user, "reset", client_ip(request))
        except AccountError:
            pass                  # same answer either way: nobody learns which emails have accounts
    return {"ok": True}


@router.post("/api/account/reset")
def reset(body: ResetIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    try:
        if user is None:
            raise AccountError("This code has expired. Ask for a new one.", field="code")
        _check_new_password(body.password, user.username, user.email)
        check_code(db, user, "reset", body.code)
        user.password_hash = hash_password(body.password)
        user.failed_logins, user.locked_until = 0, None
        user.email_verified = True        # the code proved they own the email
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))   # every other device is logged out
        db.commit()
        from app import dbarea

        dbarea._wipe_passwords(db, user)        # and typed database passwords go now, not at the next clean-up
        _start_session(db, user, request, response)
        return {"ok": True, "user": as_dict(user)}
    except AccountError as exc:
        return _err(exc)


def delete_account(db: Session, user_id: int) -> int:
    """'Delete all my data' while logged in: the account goes too (its chats were removed with the visitor id)."""
    for model in (UserSession, EmailCode):
        db.execute(delete(model).where(model.user_id == user_id))
    n = db.execute(delete(User).where(User.id == user_id)).rowcount
    db.commit()
    return n


def account_visitors(db: Session) -> set[str]:
    return set(db.scalars(select(User.visitor_id)))
