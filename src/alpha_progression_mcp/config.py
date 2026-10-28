"""Paths and client settings."""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("ALPHA_PROGRESSION_CONFIG_DIR", Path.home() / ".config" / "alpha-progression-mcp"))
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "token.json"

# Client settings used by the app (observed with version 7.7). Each can be overridden in
# CONFIG_FILE or with an ALPHA_PROGRESSION_<NAME> environment variable if the app changes them.
DEFAULTS = {
    "LOGIN_URL": "https://alphaprogression.com/api/v3/user-login",
    "LOGIN_API_KEY": "0bd62c6369b74dfcd0ea4dce15a117143b5f3cb5169fd46397581ed9df7cfbef",
    "PASSWORD_SALT": "460bd0d79294d1b35c9d934a0be51ff0086aa8f49330515a1f8c048eb3c8282c",
    "COUCH_URL": "https://8e4fc8f1-3681-4dc2-8215-45ad5f9233e6-bluemix.cloudant.com",
}
_KEYS = tuple(DEFAULTS)


class ConfigError(RuntimeError):
    pass


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        settings.update(json.loads(CONFIG_FILE.read_text()))
    for key in _KEYS:
        value = os.environ.get(f"ALPHA_PROGRESSION_{key}")
        if value:
            settings[key] = value
    missing = [k for k in _KEYS if not settings.get(k)]
    if missing:
        raise ConfigError(
            f"Missing settings: {', '.join(missing)}. Set them in {CONFIG_FILE} "
            "or as ALPHA_PROGRESSION_<NAME> environment variables (see README)."
        )
    return settings


def write_private(path: Path, data: dict) -> None:
    """Write JSON readable only by the current user."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)
