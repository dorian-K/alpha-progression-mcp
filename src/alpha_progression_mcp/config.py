"""Where the token is stored and which server settings to use."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("ALPHA_PROGRESSION_CONFIG_DIR", Path.home() / ".config" / "alpha-progression-mcp"))
TOKEN_FILE = CONFIG_DIR / "token.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Settings used by the app (version 7.7). If the app changes them, override any of them in CONFIG_FILE
# or with an ALPHA_PROGRESSION_<NAME> environment variable.
DEFAULTS = {
    "LOGIN_URL": "https://alphaprogression.com/api/v3/user-login",
    "LOGIN_API_KEY": "0bd62c6369b74dfcd0ea4dce15a117143b5f3cb5169fd46397581ed9df7cfbef",
    "PASSWORD_SALT": "460bd0d79294d1b35c9d934a0be51ff0086aa8f49330515a1f8c048eb3c8282c",
    "COUCH_URL": "https://8e4fc8f1-3681-4dc2-8215-45ad5f9233e6-bluemix.cloudant.com",
}


def settings() -> dict:
    result = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        result.update(json.loads(CONFIG_FILE.read_text()))
    for key in DEFAULTS:
        result[key] = os.environ.get(f"ALPHA_PROGRESSION_{key}", result[key])
    return result


def write_private(path: Path, data: dict) -> None:
    """Write JSON that only the current user can read."""
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
