import asyncio
import json
import pathlib
import re

import httpx
import pytest

from alpha_progression_mcp import analysis, auth, config, server


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


def _set(weight, reps, done=True, **kw):
    return {"weight": weight, "weightUnit": "kg", "reps": reps, **({"completed": 1} if done else {}), **kw}


def test_working_sets_drop_warmups_and_planned_sets():
    sets = analysis.performed([_set(20, 10), _set(40, 8), _set(60, 6), _set(60, 5), _set(60, 6, done=False)])
    assert [s["weight"] for s in analysis.working_sets(sets)] == [60, 60]


def test_e1rm_uses_rir_and_caps_reps():
    assert analysis.e1rm(_set(60, 6)) == 72.0
    assert analysis.e1rm(_set(60, 4, rir=2)) == 72.0
    assert analysis.e1rm(_set(20, 20)) is None
    assert analysis.e1rm({"reps": 10}) is None


def test_stretches_are_mobility_not_training():
    assert analysis.is_mobility("Doorway chest stretch", [_set(0, 30)])
    assert analysis.is_mobility("Child's pose", [_set(0, 30)])
    assert not analysis.is_mobility("Pull-Ups", [_set(0, 8)])


def test_fractional_sets_per_muscle():
    workout = {
        "exercises": [{"exerciseId": "exercise-28", "sets": [_set(40, 8), _set(60, 6), _set(60, 6), _set(60, 5)]}]
    }
    exercises = {
        "exercise-28": {
            "name": "Bench Press (barbell)",
            "muscle": "chest",
            "secondary_counted": ["front-delts", "triceps"],
        }
    }
    rows = analysis.exercise_rows(workout, exercises)
    assert dict(analysis.muscle_sets(rows)) == {"chest": 3, "front-delts": 1.5, "triceps": 1.5}


def test_custom_names_are_unescaped():
    assert analysis.clean_name("Child&apos;s pose") == "Child's pose"


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
