"""Read-only MCP tools for Alpha Progression data, plus the `login` and `status` commands."""

import getpass
import json
import logging
import sys
from datetime import date, datetime
from importlib.resources import files

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .auth import login
from .config import TOKEN_FILE
from .couch import Couch

# httpx logs every request URL at INFO level, and the URL contains the user's database name.
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer(
    "alpha-progression",
    version=__version__,
    instructions=(
        "Read-only access to the user's Alpha Progression strength-training data. "
        "Start with get_sessions for recent workouts, get_session for every set of one workout, "
        "and get_exercise_history for progress on one exercise. "
        "If a tool says the user is not logged in, ask them to run `alpha-progression-mcp login` in a terminal."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
couch = Couch()

# Built-in exercises: {"exercise-169": {"name": ..., "equipment": ..., "muscle": ...}}
BUILT_IN_EXERCISES = json.loads(files(__package__).joinpath("exercises.json").read_text())
LB_TO_KG = 0.45359237


# --- Helpers -----------------------------------------------------------------


def _docs(doc_type: str, refresh: bool = False) -> list[dict]:
    return [d for d in couch.all_docs(refresh) if d.get("type") == doc_type]


def _time(ms: int | None) -> datetime | None:
    """The app stores times as epoch milliseconds."""
    return datetime.fromtimestamp(ms / 1000).astimezone() if ms else None


def _iso(ms: int | None) -> str | None:
    t = _time(ms)
    return t.isoformat(timespec="minutes") if t else None


def _exercises() -> dict[str, dict]:
    """Built-in exercises plus the user's custom ones (which have their own ids)."""
    custom = {d["_id"]: {k: d.get(k) for k in ("name", "equipment", "muscle")} for d in _docs("exercise")}
    return BUILT_IN_EXERCISES | custom


def _name(exercise_id: str, exercises: dict[str, dict]) -> str:
    return exercises.get(exercise_id, {}).get("name") or exercise_id


def _kg(s: dict) -> float | None:
    if not isinstance(s.get("weight"), (int, float)):
        return None
    return s["weight"] * LB_TO_KG if s.get("weightUnit") in ("lb", "lbs") else s["weight"]


def _done(sets: list[dict] | None) -> list[dict]:
    """Performed sets. Each finished set has a `completed` timestamp; planned ones don't."""
    return [s for s in sets or [] if s.get("completed")]


def _volume(sets: list[dict]) -> int:
    return round(sum((_kg(s) or 0) * (s.get("reps") or 0) for s in sets))


def _best(sets: list[dict]) -> dict | None:
    """Heaviest set, ties broken by reps."""
    weighted = [s for s in sets if _kg(s) is not None]
    if not weighted:
        return None
    s = max(weighted, key=lambda s: (_kg(s), s.get("reps") or 0))
    return {"weight_kg": round(_kg(s), 2), "reps": s.get("reps"), "rir": s.get("rir")}


def _workouts(start_date: str = "", end_date: str = "", refresh: bool = False) -> list[dict]:
    """Performed workouts in a date range (YYYY-MM-DD, inclusive), oldest first. Skipped ones have no date."""
    start = date.fromisoformat(start_date) if start_date else date.min
    end = date.fromisoformat(end_date) if end_date else date.max
    done = [d for d in _docs("session", refresh) if d.get("date") and not d.get("skipped")]
    return sorted((d for d in done if start <= _time(d["date"]).date() <= end), key=lambda d: d["date"])


def _summary(doc: dict, exercises: dict[str, dict], plans: dict[str, str]) -> dict:
    items, all_sets = [], []
    for ex in doc.get("exercises") or []:
        sets = _done(ex.get("sets"))
        all_sets += sets
        info = exercises.get(ex.get("exerciseId"), {})
        items.append(
            {
                "exercise": _name(ex.get("exerciseId"), exercises),
                "muscle": info.get("muscle"),
                "sets": len(sets),
                "best_set": _best(sets),
            }
        )
    start, end = _time(doc.get("date")), _time(doc.get("completed"))
    return {
        "id": doc["_id"],
        "name": doc.get("name"),
        "plan": plans.get(doc.get("planId")),
        "start": _iso(doc.get("date")),
        "duration_min": round((end - start).total_seconds() / 60) if start and end else None,
        "deload": bool(doc.get("deload")),
        "total_sets": len(all_sets),
        "volume_kg": _volume(all_sets),
        "exercises": items,
    }


# --- Tools -------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def get_sessions(start_date: str = "", end_date: str = "", limit: int = 20, refresh: bool = False) -> list[dict]:
    """Performed workouts as compact summaries, newest first.

    Each has the workout and plan name, start time, duration, total sets, volume (kg x reps) and per exercise
    the target muscle, number of sets and heaviest set. Dates are YYYY-MM-DD in local time, inclusive.
    Data is cached for 5 minutes; set refresh=true to reload.
    """
    exercises, plans = _exercises(), {p["_id"]: p.get("name") for p in _docs("plan")}
    recent = _workouts(start_date, end_date, refresh)[-max(1, limit) :]
    return [_summary(d, exercises, plans) for d in reversed(recent)]


@mcp.tool(annotations=READ_ONLY)
def get_session(session_id: str) -> dict:
    """One workout with every set: weight, unit, reps, RIR (reps in reserve), duration, notes and targets."""
    doc = couch.get(session_id)
    exercises = _exercises()
    doc.pop("_rev", None)
    doc["date"], doc["completed"] = _iso(doc.get("date")), _iso(doc.get("completed"))
    for ex in doc.get("exercises") or []:
        ex["exerciseName"] = _name(ex.get("exerciseId"), exercises)
        for s in ex.get("sets") or []:
            s["completed"] = _iso(s.get("completed"))
    return doc


@mcp.tool(annotations=READ_ONLY)
def get_exercise_history(exercise: str, start_date: str = "", end_date: str = "", limit: int = 50) -> list[dict]:
    """Progress of one exercise, newest first: per workout the date, heaviest set, volume and every set.

    `exercise` is a name as shown by get_sessions (e.g. "Squats (barbell)"), case-insensitive, or an exercise id.
    Use find_exercises if unsure of the exact name.
    """
    exercises = _exercises()
    ids = {exercise} | {i for i, e in exercises.items() if (e.get("name") or "").lower() == exercise.lower()}
    rows = []
    for doc in _workouts(start_date, end_date):
        for ex in doc.get("exercises") or []:
            sets = _done(ex.get("sets"))
            if ex.get("exerciseId") in ids and sets:
                rows.append(
                    {
                        "date": _iso(doc["date"]),
                        "session_id": doc["_id"],
                        "best_set": _best(sets),
                        "volume_kg": _volume(sets),
                        "sets": [{"weight_kg": _kg(s), "reps": s.get("reps"), "rir": s.get("rir")} for s in sets],
                    }
                )
    return rows[::-1][: max(1, limit)]


@mcp.tool(annotations=READ_ONLY)
def find_exercises(query: str = "", muscle: str = "", only_used: bool = True) -> list[dict]:
    """Search exercises by name and/or target muscle (e.g. "chest", "quads", "lats"), most recently done first.

    By default only exercises the user has actually done are returned, with how often and when last.
    """
    exercises = _exercises()
    usage: dict[str, list[int]] = {}
    for doc in _workouts():
        for ex in doc.get("exercises") or []:
            if _done(ex.get("sets")):
                usage.setdefault(ex.get("exerciseId"), []).append(doc["date"])
    results = []
    for ex_id, e in exercises.items():
        name = e.get("name") or ex_id
        if query.lower() not in name.lower() or (muscle and e.get("muscle") != muscle):
            continue
        if only_used and ex_id not in usage:
            continue
        dates = usage.get(ex_id, [])
        results.append(
            {
                "id": ex_id,
                "name": name,
                "muscle": e.get("muscle"),
                "equipment": e.get("equipment"),
                "times_done": len(dates),
                "last_done": _iso(max(dates)) if dates else None,
            }
        )
    return sorted(results, key=lambda r: r["last_done"] or "", reverse=True)


@mcp.tool(annotations=READ_ONLY)
def get_plans() -> list[dict]:
    """Training plans with their days, exercises and set/rep targets. The current plan has active=true."""
    exercises = _exercises()
    plans = []
    for plan in _docs("plan"):
        plan = {k: v for k, v in plan.items() if k != "_rev"}
        for day in plan.get("days") or []:
            for ex in day.get("exercises") or []:
                ex["exerciseName"] = _name(ex.get("exerciseId"), exercises)
        plans.append(plan)
    return plans


@mcp.tool(annotations=READ_ONLY)
def get_measurements() -> list[dict]:
    """Recorded body measurements (bodyweight, body fat, waist, ...) with dates, oldest first."""
    out = []
    for doc in _docs("measurement"):
        values = sorted(doc.get("measurements") or [], key=lambda m: m.get("date") or 0)
        if values:
            out.append(
                {
                    "kind": doc["_id"].removeprefix("measurement-"),
                    "values": [
                        {"date": _iso(m.get("date")), "value": m.get("value"), "unit": m.get("unit")} for m in values
                    ],
                }
            )
    return out


@mcp.tool(annotations=READ_ONLY)
def get_document(doc_id: str) -> dict:
    """Any single stored document by id, for anything the other tools don't cover (e.g. "settings", "profile")."""
    return couch.get(doc_id)


# --- Command line ------------------------------------------------------------

USAGE = """Usage: alpha-progression-mcp [command]

  serve    Run the MCP server (default)
  login    Log in and save an access token (your email and password are not saved)
  status   Check that the saved token works
"""


def _status() -> int:
    try:
        print(f"Connected: {len(_workouts())} workouts in your account.")
        return 0
    except Exception as e:
        print(e, file=sys.stderr)
        return 1


def _login() -> int:
    print("Log in to Alpha Progression. Your email and password are used once and never saved.")
    try:
        login(input("Email: ").strip(), getpass.getpass("Password: "))
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    print(f"Logged in. Token saved to {TOKEN_FILE}.")
    return _status()


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "serve":
        mcp.run()
    elif command == "login":
        sys.exit(_login())
    elif command == "status":
        sys.exit(_status())
    elif command in ("-V", "--version"):
        print(__version__)
    else:
        print(USAGE)
        sys.exit(0 if command in ("-h", "--help", "help") else 2)
