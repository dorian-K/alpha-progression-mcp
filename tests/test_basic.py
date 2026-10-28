import json

import httpx
import pytest

from alpha_progression_mcp import auth, config, server


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "token.json")
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "token.json")
    return tmp_path


def test_defaults_need_no_config():
    assert set(config.load_settings()) == {"LOGIN_URL", "LOGIN_API_KEY", "PASSWORD_SALT", "COUCH_URL"}


def _fake_post(body):
    def post(url, data, timeout):
        return httpx.Response(200, text=json.dumps(body), request=httpx.Request("POST", url))
    return post


def test_login_saves_only_token(monkeypatch, tmp_config):
    monkeypatch.setattr(auth.httpx, "post", _fake_post({"name": "db1", "token": "t0k"}))
    auth.login("me@example.com", "secret")
    saved = (tmp_config / "token.json").read_text()
    assert "t0k" in saved and "me@example.com" not in saved and "secret" not in saved
    assert oct((tmp_config / "token.json").stat().st_mode)[-3:] == "600"


def test_login_wrong_password(monkeypatch):
    monkeypatch.setattr(auth.httpx, "post", _fake_post({"error": "incorrect_password"}))
    with pytest.raises(auth.AuthError, match="Wrong email or password"):
        auth.login("me@example.com", "nope")


def test_not_logged_in_message():
    with pytest.raises(auth.AuthError, match="login"):
        auth.load_session()


def test_session_summary():
    doc = {
        "_id": "s1", "type": "session", "name": "Day 1", "planId": "p1",
        "date": 1761649200000, "completed": 1761652800000,
        "exercises": [
            {"exerciseId": "custom-1", "sets": [
                {"weight": 100, "weightUnit": "kg", "reps": 5, "completed": 1},
                {"weight": 100, "weightUnit": "kg", "reps": 6, "completed": 2},
                {"weight": 100, "weightUnit": "kg"},  # planned, not done
            ]},
            {"exerciseId": "exercise-81", "sets": [{"weight": 20, "weightUnit": "kg", "reps": 10, "completed": 3}]},
        ],
    }
    s = server._summarize_session(doc, {"custom-1": "My squat"}, {"p1": "Push Pull"})
    assert s["duration_min"] == 60
    assert s["plan"] == "Push Pull"
    assert s["total_sets"] == 3
    assert s["volume_kg"] == 1300
    assert s["exercises"][0]["exercise"] == "My squat"
    assert s["exercises"][0]["best_set"] == {"weight_kg": 100, "reps": 6, "rir": None}


def test_all_tools_read_only():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    assert tools and all(t.annotations and t.annotations.read_only_hint for t in tools)


def test_only_allowed_endpoints():
    """The package may only call the login endpoint and read the user's own synced database.

    Paid (Pro) features such as the plan generator, and account, purchase or support endpoints,
    must never be called. Reading your own synced data is available to every account.
    """
    import pathlib
    import re

    src = pathlib.Path(server.__file__).parent
    code = "\n".join(p.read_text() for p in src.glob("*.py"))
    api_paths = set(re.findall(r"alphaprogression\.com/api/[\w/.-]+", code))
    assert api_paths == {"alphaprogression.com/api/v3/user-login"}
    assert "client.get(" in code
    for method in ("client.post(", "client.put(", "client.delete(", "client.patch(", ".request("):
        assert method not in code
