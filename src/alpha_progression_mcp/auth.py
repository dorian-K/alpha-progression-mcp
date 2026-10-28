"""Login. Only the returned database name and token are saved, never the email or password."""

import hashlib
import json
import time

import httpx

from . import config


class AuthError(RuntimeError):
    pass


def login(email: str, password: str) -> None:
    s = config.settings()
    # The server expects sha256(password + salt), not the plain password.
    hashed = hashlib.sha256((password + s["PASSWORD_SALT"]).encode()).hexdigest()
    try:
        resp = httpx.post(
            s["LOGIN_URL"], data={"apiKey": s["LOGIN_API_KEY"], "email": email, "password": hashed}, timeout=30
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise AuthError(f"Could not reach the login server: {e}") from e
    body = json.loads(resp.text or "{}")

    if body.get("name") and body.get("token"):
        config.write_private(config.TOKEN_FILE, {"name": body["name"], "token": body["token"]})
    elif body.get("timeout"):
        # The account locks for 5 minutes after too many attempts; `timeout` is when the lock started.
        minutes = max(1, round((body["timeout"] + 300_000 - time.time() * 1000) / 60_000))
        raise AuthError(f"Too many login attempts. Try again in about {minutes} minutes.")
    elif body.get("error") in ("user_not_found", "incorrect_password"):
        raise AuthError("Wrong email or password.")
    else:
        raise AuthError(f"Unexpected response from the login server: {body}")


def load_token() -> dict:
    if not config.TOKEN_FILE.exists():
        raise AuthError("Not logged in. Run `alpha-progression-mcp login` in a terminal first.")
    return json.loads(config.TOKEN_FILE.read_text())
