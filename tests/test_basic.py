import asyncio
import json
import pathlib
import re

import httpx
import pytest

from alpha_progression_mcp import auth, config, server


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "token.json")
    return tmp_path


def fake_login_response(monkeypatch, body: dict):
    def post(url, data, timeout):
        return httpx.Response(200, text=json.dumps(body), request=httpx.Request("POST", url))

    monkeypatch.setattr(auth.httpx, "post", post)


def test_login_saves_only_the_token(monkeypatch, tmp_config):
    fake_login_response(monkeypatch, {"name": "db1", "token": "t0k"})
    auth.login("me@example.com", "secret")
    token_file = tmp_config / "token.json"
    assert json.loads(token_file.read_text()) == {"name": "db1", "token": "t0k"}
    assert oct(token_file.stat().st_mode)[-3:] == "600"


def test_wrong_password(monkeypatch):
    fake_login_response(monkeypatch, {"error": "incorrect_password"})
    with pytest.raises(auth.AuthError, match="Wrong email or password"):
        auth.login("me@example.com", "nope")


def test_not_logged_in():
    with pytest.raises(auth.AuthError, match="login"):
        auth.load_token()


def test_workout_summary():
    workout = {
        "_id": "w1",
        "name": "Day 1",
        "planId": "p1",
        "date": 1761649200000,
        "completed": 1761652800000,
        "exercises": [
            {
                "exerciseId": "exercise-169",
                "sets": [
                    {"weight": 100, "weightUnit": "kg", "reps": 5, "completed": 1},
                    {"weight": 100, "weightUnit": "kg", "reps": 6, "completed": 2},
                    {"weight": 100, "weightUnit": "kg"},  # planned, not done
                ],
            },
            {"exerciseId": "custom-1", "sets": [{"weight": 20, "weightUnit": "kg", "reps": 10, "completed": 3}]},
        ],
    }
    exercises = server.BUILT_IN_EXERCISES | {"custom-1": {"name": "My curl"}}
    s = server._summary(workout, exercises, {"p1": "Push Pull"})
    assert (s["plan"], s["duration_min"], s["total_sets"], s["volume_kg"]) == ("Push Pull", 60, 3, 1300)
    assert s["exercises"][0]["exercise"] == "Squats (barbell)"
    assert s["exercises"][0]["best_set"] == {"weight_kg": 100, "reps": 6, "rir": None}
    assert s["exercises"][1]["exercise"] == "My curl"


def test_every_built_in_exercise_has_a_unique_name():
    names = [e["name"] for e in server.BUILT_IN_EXERCISES.values()]
    assert len(names) > 700
    assert all(names)
    assert len(set(names)) == len(names)


def test_all_tools_are_read_only():
    tools = asyncio.run(server.mcp.list_tools())
    assert tools
    assert all(t.annotations.read_only_hint for t in tools)


def test_only_login_and_reads_are_sent():
    """Only the login endpoint and GET requests to the user's own database. No paid (Pro) or account features."""
    code = "\n".join(p.read_text() for p in pathlib.Path(server.__file__).parent.glob("*.py"))
    assert set(re.findall(r"alphaprogression\.com/api/[\w/.-]+", code)) == {"alphaprogression.com/api/v3/user-login"}
    assert sorted(re.findall(r"httpx\.(\w+)\(", code)) == ["get", "post"]  # one login POST, reads are GET
