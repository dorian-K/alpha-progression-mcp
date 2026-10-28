"""MCP server (stdio) plus the `login` / `status` command-line entry points."""

import getpass
import logging
import sys
from collections import Counter
from datetime import date, datetime

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__
from .auth import AuthError, login
from .config import TOKEN_FILE, ConfigError
from .couch import Couch

mcp = MCPServer(
    "alpha-progression",
    version=__version__,
    instructions=(
        "Read-only access to the user's Alpha Progression strength-training data. "
        "Start with get_sessions for recent workouts (compact summaries), then get_session for the full sets. "
        "If a tool says the user is not logged in, ask them to run `alpha-progression-mcp login` in a terminal."
    ),
)
couch = Couch()
# httpx logs every request URL (which contains the database name) at INFO level.
logging.getLogger("httpx").setLevel(logging.WARNING)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)


def _docs_of_type(doc_type: str, refresh: bool = False) -> list[dict]:
    return [d for d in couch.all_docs(refresh=refresh) if d.get("type") == doc_type]


def _to_datetime(value) -> datetime | None:
    """Dates may be epoch milliseconds, epoch seconds or ISO strings. Returned in local time."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds).astimezone()
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo else dt


def _iso(value) -> str | None:
    dt = _to_datetime(value)
    return dt.isoformat(timespec="minutes") if dt else None


def _exercise_names() -> dict[str, str]:
    """Names of the user's custom exercises. Built-in exercises are reported by id."""
    return {d["_id"]: d["name"] for d in _docs_of_type("exercise") if d.get("name")}


def _plan_names() -> dict[str, str]:
    return {d["_id"]: d.get("name") for d in _docs_of_type("plan")}


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


LB_TO_KG = 0.45359237


def _kg(set_: dict) -> float | None:
    weight = _num(set_.get("weight"))
    if weight is None:
        return None
    return weight * LB_TO_KG if set_.get("weightUnit") == "lbs" or set_.get("weightUnit") == "lb" else weight


def _done_sets(exercise: dict) -> list[dict]:
    """Sets that were actually performed (the app stores a `completed` timestamp per finished set)."""
    return [s for s in exercise.get("sets") or [] if s.get("completed") or s.get("reps") or s.get("duration")]


def _best_set(sets: list[dict]) -> dict | None:
    """Heaviest set (ties broken by reps)."""
    weighted = [s for s in sets if _kg(s) is not None]
    if not weighted:
        return None
    best = max(weighted, key=lambda s: (_kg(s), _num(s.get("reps")) or 0))
    return {"weight_kg": round(_kg(best), 2), "reps": best.get("reps"), "rir": best.get("rir")}


def _summarize_session(doc: dict, names: dict[str, str], plans: dict[str, str]) -> dict:
    total_sets = 0
    volume = 0.0
    summary_exercises = []
    for ex in doc.get("exercises") or []:
        sets = _done_sets(ex)
        total_sets += len(sets)
        for s in sets:
            weight, reps = _kg(s), _num(s.get("reps"))
            if weight is not None and reps is not None:
                volume += weight * reps
        ex_id = ex.get("exerciseId")
        summary_exercises.append({"exercise": names.get(ex_id, ex_id), "sets": len(sets), "best_set": _best_set(sets)})
    start, end = _to_datetime(doc.get("date")), _to_datetime(doc.get("completed"))
    return {
        "id": doc.get("_id"),
        "name": doc.get("name"),
        "plan": plans.get(doc.get("planId"), doc.get("planId")),
        "start": _iso(doc.get("date")),
        "end": _iso(doc.get("completed")),
        "duration_min": round((end - start).total_seconds() / 60) if start and end else None,
        "week": doc.get("week"),
        "deload": bool(doc.get("deload")),
        "total_sets": total_sets,
        "volume_kg": round(volume) if volume else None,
        "exercises": summary_exercises,
    }


def _performed_sessions(refresh: bool = False) -> list[tuple[datetime, dict]]:
    """Sessions that were actually done, oldest first. Skipped or planned-only sessions have no date."""
    out = []
    for doc in _docs_of_type("session", refresh=refresh):
        dt = _to_datetime(doc.get("date"))
        if dt is None or doc.get("skipped"):
            continue
        out.append((dt, doc))
    out.sort(key=lambda p: p[0].timestamp())
    return out


def _in_range(dt: datetime, start_date: str, end_date: str) -> bool:
    if start_date and dt.date() < date.fromisoformat(start_date):
        return False
    if end_date and dt.date() > date.fromisoformat(end_date):
        return False
    return True


@mcp.tool(annotations=READ_ONLY)
def get_account_info() -> dict:
    """Check the connection: number of stored documents and of performed workouts for the logged-in account."""
    info = couch.info()
    return {"doc_count": info.get("doc_count"), "workouts": len(_performed_sessions())}


@mcp.tool(annotations=READ_ONLY)
def list_document_types(refresh: bool = False) -> dict:
    """Number of stored documents per type (session, plan, measurement, exercise, ...). Set refresh=true to bypass the 5-minute cache."""
    return dict(Counter(d.get("type", "<none>") for d in couch.all_docs(refresh=refresh)))


@mcp.tool(annotations=READ_ONLY)
def get_sessions(start_date: str = "", end_date: str = "", limit: int = 20, refresh: bool = False) -> list[dict]:
    """Performed workouts as compact summaries, newest first.

    Each summary has the workout name, plan, start/end time, duration, number of sets, volume in kg
    (weight x reps) and per exercise the number of sets and the heaviest set.
    Dates are YYYY-MM-DD in local time and inclusive; leave empty for no bound. Skipped workouts are left out.
    Built-in exercises are shown by id (e.g. "exercise-169"); custom exercises by name.
    Use get_session for every individual set.
    """
    names, plans = _exercise_names(), _plan_names()
    picked = [doc for dt, doc in _performed_sessions(refresh) if _in_range(dt, start_date, end_date)]
    return [_summarize_session(doc, names, plans) for doc in reversed(picked[-max(1, limit):])]


@mcp.tool(annotations=READ_ONLY)
def get_session(session_id: str) -> dict:
    """One workout with every exercise and set: weight, unit, reps, RIR (reps in reserve), duration, notes, targets.

    Timestamps (`date` = start, `completed` = end, per-set `completed`) are converted to local ISO times.
    """
    doc = couch.get(session_id)
    names = _exercise_names()
    doc.pop("_rev", None)
    doc["date"], doc["completed"] = _iso(doc.get("date")), _iso(doc.get("completed"))
    for ex in doc.get("exercises") or []:
        if ex.get("exerciseId") in names:
            ex["exerciseName"] = names[ex["exerciseId"]]
        for s in ex.get("sets") or []:
            if "completed" in s:
                s["completed"] = _iso(s["completed"])
    return doc


@mcp.tool(annotations=READ_ONLY)
def get_exercise_history(exercise: str, start_date: str = "", end_date: str = "", limit: int = 50) -> list[dict]:
    """Progress of one exercise over time, newest first: date, sets done, heaviest set and volume per workout.

    `exercise` is an exercise id as shown by get_sessions (e.g. "exercise-169") or a custom exercise's name.
    """
    names = _exercise_names()
    ids = {exercise} | {i for i, n in names.items() if n.lower() == exercise.lower()}
    rows = []
    for dt, doc in _performed_sessions():
        if not _in_range(dt, start_date, end_date):
            continue
        for ex in doc.get("exercises") or []:
            if ex.get("exerciseId") not in ids:
                continue
            sets = _done_sets(ex)
            if not sets:
                continue
            volume = sum((_kg(s) or 0) * (_num(s.get("reps")) or 0) for s in sets)
            rows.append({
                "date": _iso(doc.get("date")),
                "session_id": doc.get("_id"),
                "sets": len(sets),
                "best_set": _best_set(sets),
                "volume_kg": round(volume) if volume else None,
                "all_sets": [{"weight_kg": round(_kg(s), 2) if _kg(s) is not None else None, "reps": s.get("reps"), "rir": s.get("rir")} for s in sets],
            })
    return list(reversed(rows[-max(1, limit):]))


@mcp.tool(annotations=READ_ONLY)
def get_plans() -> list[dict]:
    """The user's training plans with their days, exercises and weekly set/rep targets. The active plan has active=true."""
    names = _exercise_names()
    plans = []
    for doc in _docs_of_type("plan"):
        doc = {k: v for k, v in doc.items() if k != "_rev"}
        for day in doc.get("days") or []:
            for ex in day.get("exercises") or []:
                if ex.get("exerciseId") in names:
                    ex["exerciseName"] = names[ex["exerciseId"]]
        plans.append(doc)
    return plans


@mcp.tool(annotations=READ_ONLY)
def get_measurements(include_empty: bool = False) -> list[dict]:
    """Body measurements (bodyweight, body fat, waist, arms, ...) with every recorded value and date, oldest first."""
    out = []
    for doc in _docs_of_type("measurement"):
        values = [
            {"date": _iso(m.get("date")), "value": m.get("value"), "unit": m.get("unit")}
            for m in sorted(doc.get("measurements") or [], key=lambda m: m.get("date") or 0)
        ]
        if values or include_empty:
            out.append({"kind": doc["_id"].removeprefix("measurement-"), "values": values})
    return out


@mcp.tool(annotations=READ_ONLY)
def get_custom_exercises(include_deleted: bool = False) -> list[dict]:
    """Exercises the user created themselves: id, name, equipment, primary and secondary muscles."""
    keep = ("_id", "name", "equipment", "muscle", "secondary", "deleted")
    return [
        {k: d.get(k) for k in keep if k in d}
        for d in _docs_of_type("exercise")
        if include_deleted or not d.get("deleted")
    ]


@mcp.tool(annotations=READ_ONLY)
def get_document(doc_id: str) -> dict:
    """Any single stored document by its id. Use when the other tools don't cover something."""
    return couch.get(doc_id)


def _cmd_login() -> int:
    print("Log in to Alpha Progression. Your email and password are used once and never saved.")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    try:
        session = login(email, password)
    except (AuthError, ConfigError) as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # network problems etc.
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    print(f"Logged in. Token saved to {TOKEN_FILE}.")
    return _cmd_status()


def _cmd_status() -> int:
    try:
        info = couch.info()
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Connected: {info.get('doc_count')} documents in your account.")
    return 0


USAGE = """Usage: alpha-progression-mcp [command]

Commands:
  serve    Run the MCP server over stdio (default)
  login    Log in and save an access token (email and password are not saved)
  status   Check that the saved token works
"""


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command in ("-h", "--help", "help"):
        print(USAGE)
        return
    if command in ("-V", "--version"):
        print(__version__)
        return
    if command == "login":
        sys.exit(_cmd_login())
    if command == "status":
        sys.exit(_cmd_status())
    if command == "serve":
        mcp.run()
        return
    print(USAGE, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
