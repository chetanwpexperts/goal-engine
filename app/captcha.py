"""Our own picture captcha: no outside service, nothing to load from anyone else.

The picture is an SVG made of drawn strokes (our own little stroke alphabet), not text, so a bot can't read the code
out of the page. Each character is turned, stretched and shaken differently, with noise lines over it.

The code itself is never sent to the browser and never stored: the browser gets a signed token
(nonce . expiry . HMAC(secret, nonce . expiry . CODE)). The server checks the typed code against the signature.
A token works once (its nonce is recorded in auth_attempts, shared by every app process) and for 10 minutes.
"""

import base64
import hashlib
import hmac
import math
import os
import random
import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

LIFETIME = 600                 # seconds
LENGTH = 5
ALPHABET = "2345678ACDEFHKMNPRTUVWXY"     # nothing that looks like another (no 0/O, 1/I/L, B/8, S/5, Z/2, G/6, 9/g)

# each character: strokes of points on an 8 x 12 grid (y grows downwards)
GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "2": [[(0, 3), (1, 0.6), (4, 0), (7, 1), (8, 3.5), (7, 6), (0, 12), (8, 12)]],
    "3": [[(0, 1), (4, 0), (7.5, 1.5), (7, 5), (3, 6)], [(3, 6), (7.5, 7.5), (8, 10), (5, 12), (0, 11)]],
    "4": [[(6, 12), (6, 0), (0, 8.5), (8, 8.5)]],
    "5": [[(8, 0), (1, 0), (0, 5.5), (4, 4.8), (7.5, 6.5), (8, 9.5), (5, 12), (0, 11)]],
    "6": [[(7, 0.5), (4, 0), (1, 2), (0, 7), (1, 11), (4, 12), (7, 11), (8, 8), (6, 5.5), (3, 5.5), (0, 7.5)]],
    "7": [[(0, 0), (8, 0), (3, 12)]],
    "8": [[(4, 6), (1, 4.5), (1, 1.5), (4, 0), (7, 1.5), (7, 4.5), (4, 6), (0.5, 8), (0.5, 11), (4, 12), (7.5, 11), (7.5, 8), (4, 6)]],
    "A": [[(0, 12), (4, 0), (8, 12)], [(1.5, 7.5), (6.5, 7.5)]],
    "C": [[(8, 1.5), (5, 0), (2, 0.8), (0, 4), (0, 8), (2, 11.2), (5, 12), (8, 10.5)]],
    "D": [[(0, 0), (0, 12), (4, 12), (7, 10), (8, 6), (7, 2), (4, 0), (0, 0)]],
    "E": [[(8, 0), (0, 0), (0, 12), (8, 12)], [(0, 6), (6, 6)]],
    "F": [[(8, 0), (0, 0), (0, 12)], [(0, 6), (6, 6)]],
    "H": [[(0, 0), (0, 12)], [(8, 0), (8, 12)], [(0, 6), (8, 6)]],
    "K": [[(0, 0), (0, 12)], [(8, 0), (0, 7.5)], [(2.6, 5.2), (8, 12)]],
    "M": [[(0, 12), (0, 0), (4, 7), (8, 0), (8, 12)]],
    "N": [[(0, 12), (0, 0), (8, 12), (8, 0)]],
    "P": [[(0, 12), (0, 0), (6, 0), (8, 2), (8, 4.5), (6, 6.5), (0, 6.5)]],
    "R": [[(0, 12), (0, 0), (6, 0), (8, 2), (8, 4.5), (6, 6.5), (0, 6.5)], [(4, 6.5), (8, 12)]],
    "T": [[(0, 0), (8, 0)], [(4, 0), (4, 12)]],
    "U": [[(0, 0), (0, 9), (2, 12), (6, 12), (8, 9), (8, 0)]],
    "V": [[(0, 0), (4, 12), (8, 0)]],
    "W": [[(0, 0), (2, 12), (4, 4), (6, 12), (8, 0)]],
    "X": [[(0, 0), (8, 12)], [(8, 0), (0, 12)]],
    "Y": [[(0, 0), (4, 6), (8, 0)], [(4, 6), (4, 12)]],
}
W, H = 220, 70
INKS = ("#1B2340", "#2E3A6E", "#3D2B63", "#1F4D5A", "#5A2E2E")


class CaptchaError(ValueError):
    pass


def _secret() -> bytes:
    """APP_SECRET (the same one accounts use). Without one, something every app process shares and no visitor knows."""
    key = os.getenv("APP_SECRET", "")
    if len(key) >= 16:
        return key.encode()
    return hashlib.sha256(("captcha:" + os.getenv("DATABASE_URL", "") + os.getenv("ADMIN_KEY", "")).encode()).digest()


def _sign(nonce: str, expires: int, code: str) -> str:
    return hmac.new(_secret(), f"{nonce}.{expires}.{code.upper()}".encode(), "sha256").hexdigest()[:32]


def _path(points: list[tuple[float, float]]) -> str:
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def picture(code: str, rng: random.Random | None = None) -> str:
    """The code drawn as an SVG of strokes, distorted, with noise."""
    rng = rng or random.SystemRandom()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
             f'<rect width="{W}" height="{H}" fill="#F3F5FA"/>']
    for _ in range(6):                                     # faint background arcs
        x1, y1, x2, y2 = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0, W), rng.uniform(0, H)
        parts.append(f'<path d="M{x1:.1f} {y1:.1f} Q{rng.uniform(0, W):.1f} {rng.uniform(0, H):.1f} {x2:.1f} {y2:.1f}" '
                     f'stroke="#C9D0E2" stroke-width="{rng.uniform(1, 2.5):.1f}" fill="none"/>')
    step = (W - 30) / len(code)
    for i, ch in enumerate(code):
        scale = rng.uniform(3.0, 3.6)
        sx = scale * rng.uniform(0.8, 1.0)
        angle = math.radians(rng.uniform(-18, 18))
        skew = rng.uniform(-0.25, 0.25)
        cx = 15 + step * i + step / 2 + rng.uniform(-2, 2)
        cy = H / 2 + rng.uniform(-6, 6)
        ink = rng.choice(INKS)
        width = rng.uniform(2.6, 3.6)
        for stroke in GLYPHS[ch]:
            pts = []
            for gx, gy in stroke:
                x, y = (gx - 4) * sx, (gy - 6) * scale
                x += skew * y
                x, y = x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)
                pts.append((cx + x + rng.uniform(-0.9, 0.9), cy + y + rng.uniform(-0.9, 0.9)))
            parts.append(f'<path d="{_path(pts)}" stroke="{ink}" stroke-width="{width:.1f}" fill="none" '
                         'stroke-linecap="round" stroke-linejoin="round"/>')
    for _ in range(3):                                     # lines across the characters
        y1, y2 = rng.uniform(12, H - 12), rng.uniform(12, H - 12)
        parts.append(f'<path d="M0 {y1:.1f} C{W / 3:.1f} {rng.uniform(0, H):.1f} {2 * W / 3:.1f} {rng.uniform(0, H):.1f} {W} {y2:.1f}" '
                     f'stroke="{rng.choice(INKS)}" stroke-width="{rng.uniform(1, 1.8):.1f}" fill="none" opacity="0.7"/>')
    for _ in range(40):                                    # specks
        parts.append(f'<circle cx="{rng.uniform(0, W):.1f}" cy="{rng.uniform(0, H):.1f}" r="{rng.uniform(0.6, 1.6):.1f}" '
                     f'fill="{rng.choice(INKS)}" opacity="0.55"/>')
    parts.append("</svg>")
    return "".join(parts)


def new(now: float | None = None) -> dict:
    """A fresh captcha: the picture (as a data: URL for an <img>) and the signed token. The code goes nowhere else."""
    rng = random.SystemRandom()
    code = "".join(rng.choice(ALPHABET) for _ in range(LENGTH))
    nonce = secrets.token_hex(8)
    expires = int(now or time.time()) + LIFETIME
    svg = picture(code, rng)
    return {"token": f"{nonce}.{expires}.{_sign(nonce, expires, code)}",
            "image": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()}


def check(db: Session, token: str, answer: str, now: float | None = None) -> None:
    """Raises CaptchaError unless the typed code fits the token, the token is fresh and it wasn't used before.
    Right or wrong, a token is spent by trying it: a wrong answer means a new picture."""
    from app.models import AuthAttempt, utcnow

    try:
        nonce, expires_s, sig = (token or "").split(".")
        expires = int(expires_s)
    except ValueError:
        raise CaptchaError("Please type the characters in the picture.")
    if len(nonce) != 16 or len(sig) != 32:
        raise CaptchaError("Please type the characters in the picture.")
    if expires < (now or time.time()):
        raise CaptchaError("That picture expired. Here's a new one.")
    key = f"captcha:{nonce}"
    if db.scalar(select(AuthAttempt.id).where(AuthAttempt.key == key)) is not None:
        raise CaptchaError("That picture was already used. Here's a new one.")
    db.add(AuthAttempt(key=key, at=utcnow()))
    db.commit()
    typed = "".join((answer or "").split()).upper()
    if len(typed) != LENGTH or not hmac.compare_digest(_sign(nonce, expires, typed), sig):
        raise CaptchaError("The characters didn't match. Please try the new picture.")
