"""Login flow. Only the resulting database name and token are stored, never the email or password."""

import hashlib
import json
import time

import httpx

from .config import TOKEN_FILE, load_settings, write_private


class AuthError(RuntimeError):
    pass


def login(email: str, password: str) -> dict:
    settings = load_settings()
    hashed = hashlib.sha256((password + settings["PASSWORD_SALT"]).encode()).hexdigest()
    try:
        resp = httpx.post(
            settings["LOGIN_URL"],
            data={"apiKey": settings["LOGIN_API_KEY"], "email": email, "password": hashed},
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise AuthError(f"Could not reach the Alpha Progression login server: {e}") from e
    body = json.loads(resp.text) if resp.text.strip() else {}

    if body.get("name") and body.get("token"):
        session = {"name": body["name"], "token": body["token"], "created": int(time.time())}
        write_private(TOKEN_FILE, session)
        return session
    if body.get("timeout"):
        wait_min = max(0, (body["timeout"] + 300_000 - time.time() * 1000) / 60_000)
        raise AuthError(f"Too many login attempts. Account locked for about {wait_min:.0f} more minutes.")
    if body.get("error") in ("user_not_found", "incorrect_password"):
        raise AuthError("Wrong email or password.")
    raise AuthError(f"Unexpected login response: {body}")


def load_session() -> dict:
    if not TOKEN_FILE.exists():
        raise AuthError("Not logged in. Run `alpha-progression-mcp login` first.")
    session = json.loads(TOKEN_FILE.read_text())
    if not session.get("name") or not session.get("token"):
        raise AuthError(f"{TOKEN_FILE} is invalid. Run `alpha-progression-mcp login` again.")
    return session
