import asyncio
import json
import pathlib
import re

import httpx
import pytest

from alpha_progression_mcp import analysis, auth, config, matching, plans, server


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
    ramp_then_backoff = analysis.performed(
        [_set(20, 10), _set(60, 8), _set(90, 6), _set(110, 5), _set(110, 4), _set(90, 8)]
    )
    assert [s["weight"] for s in analysis.working_sets(ramp_then_backoff)] == [110, 110, 90]


def test_e1rm_uses_rir_and_caps_reps():
    assert analysis.e1rm(_set(60, 6)) == 72.0
    assert analysis.e1rm(_set(60, 4, rir=2)) == 72.0
    assert analysis.e1rm(_set(5, 15)) == 7.5
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


def test_only_plan_tools_write():
    tools = {t.name: t.annotations for t in asyncio.run(server.mcp.list_tools())}
    for name in ("set_plan_targets", "add_plan_exercise", "swap_plan_exercise", "remove_plan_exercise"):
        assert not tools.pop(name).read_only_hint
    assert tools
    assert all(a.read_only_hint for a in tools.values())


def test_only_login_and_own_database_are_contacted():
    """The login endpoint plus the user's own database. No paid (Pro) or account endpoints."""
    code = "\n".join(p.read_text() for p in pathlib.Path(server.__file__).parent.glob("*.py"))
    assert set(re.findall(r"alphaprogression\.com/api/[\w/.-]+", code)) == {"alphaprogression.com/api/v3/user-login"}
    assert sorted(re.findall(r"httpx\.(\w+)\(", code)) == ["post", "request"]
    assert re.findall(r'_request\("(\w+)"', code) == ["GET", "GET", "PUT"]


def test_only_plans_can_be_written():
    with pytest.raises(ValueError, match="Only training plans"):
        server.couch.put({"_id": "settings", "type": None})


PLAN = {
    "_id": "p1",
    "type": "plan",
    "days": [
        {"name": "Day 1", "exercises": [{"_id": "e1", "exerciseId": "exercise-28", "weeks": [{"sets": 3, "reps": 6}]}]}
    ],
}
EXERCISES = {
    "exercise-28": {"name": "Bench Press (barbell)"},
    "exercise-30": {"name": "Bench Press (smith machine)"},
    "exercise-169": {"name": "Squats (barbell)"},
    "exercise-251": {"name": "Rows with Close Grip (cable)"},
}


def test_plan_edits_are_targeted():
    new = plans.edit(PLAN, EXERCISES, "set", "1", "bench press barbell", sets=4, reps=8)
    new = plans.edit(new, EXERCISES, "add", "day1", "squats", sets=3, reps=5, after="bench")
    new = plans.edit(new, EXERCISES, "swap", "Day 1", "Squats (barbell)", new_exercise="rows close grip")
    assert [(e["exerciseId"], e["weeks"]) for e in new["days"][0]["exercises"]] == [
        ("exercise-28", [{"sets": 4, "reps": 8}]),
        ("exercise-251", [{"sets": 3, "reps": 5}]),
    ]
    assert PLAN["days"][0]["exercises"][0]["weeks"] == [{"sets": 3, "reps": 6}]  # original untouched
    assert plans.edit(new, EXERCISES, "remove", "1", "rows")["days"][0]["exercises"] == new["days"][0]["exercises"][:1]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("set", "Day 1", "bench", {"sets": 50}), "sets must be"),
        (("set", "Day 7", "bench", {"sets": 3}), "Days in this plan: Day 1"),
        (("set", "Day 1", "squats", {"sets": 3}), "not on Day 1"),
        (("add", "Day 1", "bench press barbell", {"sets": 3, "reps": 5}), "already on Day 1"),
        (("add", "Day 1", "bench press", {"sets": 3, "reps": 5}), "matches several"),
        (("add", "Day 1", "Bnch Pres", {"sets": 3, "reps": 5}), "Did you mean"),
    ],
)
def test_bad_plan_edits_explain_themselves(args, message):
    op, day, exercise, kw = args
    with pytest.raises(matching.InputError, match=message):  # PlanEditError is an InputError
        plans.edit(PLAN, EXERCISES, op, day, exercise, **kw)


@pytest.mark.parametrize(
    "plan",
    [
        {**PLAN, "periodisation": True},
        {**PLAN, "planGenerator": True},
        {**PLAN, "days": [{"name": "Day 1", "exercises": [{"exerciseId": "x", "weeks": [{"sets": 3}, {"sets": 4}]}]}]},
    ],
)
def test_pro_plans_are_refused(plan):
    with pytest.raises(plans.PlanEditError, match="Pro features"):
        plans.edit(plan, EXERCISES, "set", "Day 1", "bench", sets=3)


def test_input_checks():
    with pytest.raises(matching.InputError, match="Groups: arms"):
        matching.check_muscle("pecs")
    with pytest.raises(matching.InputError, match="like 2026-10-28"):
        matching.parse_date("28.10.2026", "start_date")


def test_ambiguous_names_resolve_to_the_clearly_most_used():
    usage = {"exercise-28": 136, "exercise-30": 3}
    assert matching.match_exercise("bench press", EXERCISES, usage) == {"exercise-28"}
    with pytest.raises(matching.InputError, match="matches several"):
        matching.match_exercise("bench press", EXERCISES, {"exercise-28": 4, "exercise-30": 3})
