"""MCP tools for Alpha Progression data (read-only, plus plan editing) and the `login` and `status` commands."""

import getpass
import json
import logging
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from importlib.resources import files

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, config, plans
from . import analysis as a
from .auth import login
from .couch import Couch
from .matching import check_muscle, match_exercise, norm, parse_date

# httpx logs every request URL at INFO level, and the URL contains the user's database name.
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer(
    "alpha-progression",
    version=__version__,
    instructions=(
        "Read-only access to the user's Alpha Progression strength-training data. "
        "For training guidance start with get_weekly_training (sets per muscle per week, frequency) and "
        "get_progress (which exercises are improving or stalled). Use get_sessions for recent workouts, "
        "get_exercise_history for one lift over time and get_session for every set of one workout. "
        "Working sets exclude warm-ups (lighter sets logged before the first heavy set) and stretches. "
        "Estimated 1RM (e1RM) uses the Epley formula. "
        "To change the plan use set_plan_targets, add_plan_exercise, swap_plan_exercise or remove_plan_exercise: "
        "one change per call, "
        "first without apply to get a preview, then with apply=true only after the user approved it. "
        "Names may be partial; if a tool returns an error it lists the valid options. "
        "If a tool says the user is not logged in, ask them to run `alpha-progression-mcp login` in a terminal."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
couch = Couch()

# Built-in exercises: {"exercise-169": {"name", "equipment", "muscle", "secondary"}}
BUILT_IN_EXERCISES = json.loads(files(__package__).joinpath("exercises.json").read_text())


# --- Data access ---------------------------------------------------------------


def _docs(doc_type: str, refresh: bool = False) -> list[dict]:
    return [d for d in couch.all_docs(refresh) if d.get("type") == doc_type]


def _exercises() -> dict[str, dict]:
    """Built-in and custom exercises by id, with the secondary muscles that count for volume."""
    out = {i: {**e, "secondary_counted": e["secondary"][: a.SECONDARY_COUNTED]} for i, e in BUILT_IN_EXERCISES.items()}
    for d in _docs("exercise"):
        out[d["_id"]] = {
            "name": a.clean_name(d.get("name")),
            "equipment": d.get("equipment"),
            "muscle": d.get("muscle"),
            "secondary": d.get("secondary") or [],
            "secondary_counted": d.get("secondary") or [],  # chosen by the user, so all count
        }
    return out


def _range(start_date: str, end_date: str, default_days: int | None) -> tuple[date, date]:
    end = parse_date(end_date, "end_date") if end_date else date.today()
    if start_date:
        start = parse_date(start_date, "start_date")
    else:
        start = end - timedelta(days=default_days) if default_days else date.min
    return start, end


def _workouts(start: date = date.min, end: date = date.max, refresh: bool = False) -> list[dict]:
    """Performed workouts in [start, end], oldest first. Skipped ones have no date."""
    done = [d for d in _docs("session", refresh) if d.get("date") and not d.get("skipped")]
    return sorted((d for d in done if start <= a.to_time(d["date"]).date() <= end), key=lambda d: d["date"])


def _usage() -> dict[str, int]:
    """How many workouts each exercise was done in."""
    return dict(
        Counter(
            ex.get("exerciseId") for w in _workouts() for ex in w.get("exercises") or [] if a.performed(ex.get("sets"))
        )
    )


def _resolve(exercise: str, exercises: dict[str, dict]) -> set[str]:
    """Exercise ids for a name or id; partial names resolve among the exercises the user does most."""
    return match_exercise(exercise, exercises, _usage())


# --- Tools -------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def get_sessions(start_date: str = "", end_date: str = "", limit: int = 20, refresh: bool = False) -> list[dict]:
    """Performed workouts as compact summaries, newest first.

    Per workout: name, plan, start, duration, working sets, volume (kg x reps), and per exercise the muscle,
    working sets, top set and e1RM. `skipped` lists exercises that were planned but not done; `mobility`
    lists stretches (not counted as training). Dates are YYYY-MM-DD, inclusive. Cached 5 min; refresh=true reloads.
    """
    start, end = _range(start_date, end_date, None)
    exercises = _exercises()
    plans = {p["_id"]: p.get("name") for p in _docs("plan")}
    out = []
    for w in reversed(_workouts(start, end, refresh)[-max(1, limit) :]):
        rows = a.exercise_rows(w, exercises)
        training = [r for r in rows if r["sets"] and not r["mobility"]]
        begin, finish = a.to_time(w.get("date")), a.to_time(w.get("completed"))
        out.append(
            {
                "id": w["_id"],
                "name": w.get("name"),
                "plan": plans.get(w.get("planId")),
                "start": a.iso(w.get("date")),
                "duration_min": round((finish - begin).total_seconds() / 60) if begin and finish else None,
                "deload": bool(w.get("deload")),
                "working_sets": sum(len(r["working"]) for r in training),
                "volume_kg": sum(a.volume(r["sets"]) for r in training),
                "exercises": [
                    {
                        "exercise": r["name"],
                        "muscle": r["muscle"],
                        "working_sets": len(r["working"]),
                        "top_set": a.top_set(r["working"]),
                        "e1rm_kg": r["e1rm"],
                    }
                    for r in training
                ],
                "skipped": [r["name"] for r in rows if not r["sets"]],
                "mobility": [r["name"] for r in rows if r["sets"] and r["mobility"]],
            }
        )
    return out


@mcp.tool(annotations=READ_ONLY)
def get_session(session_id: str) -> dict:
    """One workout with every set: weight, unit, reps, RIR (reps in reserve), duration, notes and targets."""
    doc = couch.get(session_id)
    exercises = _exercises()
    doc.pop("_rev", None)
    doc["date"], doc["completed"] = a.iso(doc.get("date")), a.iso(doc.get("completed"))
    for ex in doc.get("exercises") or []:
        ex["exerciseName"] = exercises.get(ex.get("exerciseId"), {}).get("name") or ex.get("exerciseId")
        for s in ex.get("sets") or []:
            s["completed"] = a.iso(s.get("completed"))
    return doc


@mcp.tool(annotations=READ_ONLY)
def get_weekly_training(
    start_date: str = "", end_date: str = "", muscle: str = "", skip_empty_weeks: bool = True
) -> dict:
    """Weekly training load, the main input for volume and frequency guidance. Defaults to the last 8 weeks.

    Per week (Monday start): workouts, minutes, working sets, and per muscle the fractional working sets
    (1 per set for the target muscle, 0.5 for its main secondary muscles) and frequency (days trained).
    `average_per_week` gives the same per muscle averaged over the range, with its muscle group. Weeks without
    any workout (holiday, illness) are left out of the average unless skip_empty_weeks=false.
    Filter with `muscle` (e.g. "chest") or a group: chest, back, shoulders, arms, legs, core.
    """
    muscle = check_muscle(muscle)
    start, end = _range(start_date, end_date, 7 * 8 - 1)
    start = a.week_start(start)
    exercises = _exercises()
    weeks: dict[date, dict] = {}
    d = start
    while d <= end:
        weeks[d] = {
            "workouts": 0,
            "minutes": 0,
            "working_sets": 0,
            "sets": defaultdict(float),
            "days": defaultdict(set),
        }
        d += timedelta(days=7)
    for w in _workouts(start, end):
        day = a.to_time(w["date"]).date()
        wk = weeks[a.week_start(day)]
        rows = a.exercise_rows(w, exercises)
        wk["workouts"] += 1
        if w.get("completed"):
            wk["minutes"] += round((w["completed"] - w["date"]) / 60000)
        wk["working_sets"] += sum(len(r["working"]) for r in rows if not r["mobility"])
        for m, n in a.muscle_sets(rows).items():
            wk["sets"][m] += n
            wk["days"][m].add(day)

    def keep(m: str) -> bool:
        return not muscle or muscle in (m, a.MUSCLE_GROUPS.get(m))

    muscles = sorted(
        {m for wk in weeks.values() for m in wk["sets"] if keep(m)}, key=lambda m: (a.MUSCLE_GROUPS.get(m, "z"), m)
    )
    counted = [wk for wk in weeks.values() if wk["workouts"] or not skip_empty_weeks]
    n = max(1, len(counted))
    return {
        "range": [start.isoformat(), end.isoformat()],
        "weeks_averaged": len(counted),
        "weeks_without_workouts": sum(1 for wk in weeks.values() if not wk["workouts"]),
        "average_per_week": [
            {
                "muscle": m,
                "group": a.MUSCLE_GROUPS.get(m),
                "sets": round(sum(wk["sets"][m] for wk in counted) / n, 1),
                "frequency": round(sum(len(wk["days"][m]) for wk in counted) / n, 1),
            }
            for m in muscles
        ],
        "weeks": [
            {
                "week": k.isoformat(),
                "workouts": wk["workouts"],
                "minutes": wk["minutes"],
                "working_sets": wk["working_sets"],
                "sets_per_muscle": {m: round(wk["sets"][m], 1) for m in muscles if wk["sets"][m]},
                "frequency": {m: len(wk["days"][m]) for m in muscles if wk["days"][m]},
            }
            for k, wk in weeks.items()
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def get_progress(start_date: str = "", end_date: str = "", muscle: str = "", min_sessions: int = 3) -> list[dict]:
    """Which exercises are improving or stalled. Defaults to the last 6 months.

    Per exercise done at least `min_sessions` times: sessions, working sets per week, e1RM at the start and
    end of the range (median of the first and last 3 sessions, to smooth out single days), best e1RM,
    change in % and the last date. Unweighted exercises compare top-set reps instead.
    Sorted by muscle group. Filter with `muscle` or a group (chest, back, shoulders, arms, legs, core).
    """
    muscle = check_muscle(muscle)
    start, end = _range(start_date, end_date, 182)
    exercises = _exercises()
    history: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for w in _workouts(start, end):
        for r in a.exercise_rows(w, exercises):
            if r["working"] and not r["mobility"]:
                history[r["id"]].append((w["date"], r))
    weeks = max(1.0, ((end - start).days + 1) / 7)
    out = []
    for ex_id, rows in history.items():
        info = exercises.get(ex_id, {})
        m = info.get("muscle")
        if len(rows) < min_sessions or (muscle and muscle not in (m, a.MUSCLE_GROUPS.get(m))):
            continue
        # Compare e1RM when most sessions have one, else top-set reps (bodyweight exercises).
        weighted = sum(1 for _, r in rows if r["e1rm"]) >= len(rows) * 2 / 3
        metric = [r["e1rm"] if weighted else (a.top_set(r["working"]) or {}).get("reps") or 0 for _, r in rows]
        metric = [m for m in metric if m]
        if len(metric) < min_sessions:
            continue
        first, last = statistics.median(metric[:3]), statistics.median(metric[-3:])
        out.append(
            {
                "exercise": info.get("name") or ex_id,
                "muscle": m,
                "group": a.MUSCLE_GROUPS.get(m),
                "metric": "e1rm_kg" if weighted else "top_set_reps",
                "sessions": len(rows),
                "working_sets_per_week": round(sum(len(r["working"]) for _, r in rows) / weeks, 1),
                "start": first,
                "end": last,
                "best": max(metric),
                "change_pct": round((last - first) / first * 100, 1) if first else None,
                "last_done": a.iso(rows[-1][0]),
            }
        )
    return sorted(out, key=lambda r: (r["group"] or "z", r["muscle"] or "", -(r["sessions"])))


@mcp.tool(annotations=READ_ONLY)
def get_exercise_history(
    exercise: str, start_date: str = "", end_date: str = "", limit: int = 100, include_sets: bool = False
) -> dict:
    """One exercise over time: a summary plus one row per workout (newest first) with the top set,
    e1RM, working sets and volume. `exercise` is a name (partial names like "bench press" work if they are
    unambiguous) or an id. include_sets=true adds every set, including warm-ups.
    """
    start, end = _range(start_date, end_date, None)
    exercises = _exercises()
    ids = _resolve(exercise, exercises)
    rows = []
    for w in _workouts(start, end):
        for r in a.exercise_rows(w, exercises):
            if r["id"] in ids and r["working"]:
                row = {
                    "date": a.iso(w["date"]),
                    "session_id": w["_id"],
                    "top_set": a.top_set(r["working"]),
                    "e1rm_kg": r["e1rm"],
                    "working_sets": len(r["working"]),
                    "volume_kg": a.volume(r["sets"]),
                }
                if include_sets:
                    row["sets"] = [
                        {"weight_kg": a.kg(s), "reps": s.get("reps"), "rir": s.get("rir")} for s in r["sets"]
                    ]
                rows.append(row)
    e = [r["e1rm_kg"] for r in rows if r["e1rm_kg"]]
    best = max(rows, key=lambda r: r["e1rm_kg"] or 0, default=None)
    info = exercises[sorted(ids)[0]]
    return {
        "exercise": info.get("name") or exercise,
        "muscle": info.get("muscle"),
        "secondary": info.get("secondary"),
        "summary": {
            "sessions": len(rows),
            "first": rows[0]["date"] if rows else None,
            "last": rows[-1]["date"] if rows else None,
            "best_e1rm": {"kg": best["e1rm_kg"], "date": best["date"], "top_set": best["top_set"]} if e else None,
            "recent_e1rm_kg": statistics.median(e[-3:]) if e else None,
        },
        "sessions": rows[::-1][: max(1, limit)],
    }


@mcp.tool(annotations=READ_ONLY)
def find_exercises(query: str = "", muscle: str = "", only_used: bool = True) -> list[dict]:
    """Search exercises by name and/or muscle or group (e.g. "chest", "quads", "legs"), most recently done first.

    By default only exercises the user has done, with how often and when last.
    """
    muscle, words = check_muscle(muscle), norm(query).split()
    exercises = _exercises()
    usage: dict[str, list[int]] = defaultdict(list)
    for w in _workouts():
        for ex in w.get("exercises") or []:
            if a.performed(ex.get("sets")):
                usage[ex.get("exerciseId")].append(w["date"])
    results = []
    for ex_id, e in exercises.items():
        name, m = e.get("name") or ex_id, e.get("muscle")
        if not all(w in norm(name) for w in words) or (muscle and muscle not in (m, a.MUSCLE_GROUPS.get(m))):
            continue
        if only_used and ex_id not in usage:
            continue
        dates = usage.get(ex_id, [])
        results.append(
            {
                "id": ex_id,
                "name": name,
                "muscle": m,
                "secondary": e.get("secondary"),
                "equipment": e.get("equipment"),
                "times_done": len(dates),
                "last_done": a.iso(max(dates)) if dates else None,
            }
        )
    return sorted(results, key=lambda r: r["last_done"] or "", reverse=True)


@mcp.tool(annotations=READ_ONLY)
def get_plans(active_only: bool = True) -> list[dict]:
    """Training plans: per day the exercises with their muscle and weekly set/rep/RIR targets.
    Only the current plan by default; active_only=false returns all plans.
    """
    exercises = _exercises()
    out = []
    for p in _docs("plan"):
        if active_only and not p.get("active"):
            continue
        days = [
            {
                "day": day.get("name"),
                "exercises": [
                    {
                        "exercise": exercises.get(ex.get("exerciseId"), {}).get("name") or ex.get("exerciseId"),
                        "muscle": exercises.get(ex.get("exerciseId"), {}).get("muscle"),
                        "targets_by_week": ex.get("weeks"),
                    }
                    for ex in day.get("exercises") or []
                ],
            }
            for day in p.get("days") or []
        ]
        out.append(
            {
                "id": p["_id"],
                "name": p.get("name"),
                "active": bool(p.get("active")),
                "created": a.iso(p.get("date")),
                "duration_weeks": p.get("planDuration"),
                "days": days,
            }
        )
    return out


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
                        {"date": a.iso(m.get("date")), "value": m.get("value"), "unit": m.get("unit")} for m in values
                    ],
                }
            )
    return out


@mcp.tool(annotations=READ_ONLY)
def get_document(doc_id: str) -> dict:
    """Any single stored document by id, for anything the other tools don't cover (e.g. "settings", "profile")."""
    return couch.get(doc_id)


# --- Plan editing (turned off by ALPHA_PROGRESSION_READ_ONLY=1) -------------------------


def _plan_diff(before: dict, after: dict, exercises: dict[str, dict]) -> list[str]:
    """Readable list of what changed, e.g. "Day 1: Bench Press (barbell) 5 x 10 -> 4 x 6"."""

    def describe(entry: dict) -> tuple[str, str]:
        w = (entry.get("weeks") or [{}])[0]
        name = exercises.get(entry.get("exerciseId"), {}).get("name") or entry.get("exerciseId")
        amount = f"{w.get('duration')} s" if w.get("duration") else w.get("reps")
        return name, f"{w.get('sets')} x {amount}"

    out = []
    for old_day, new_day in zip(before["days"], after["days"], strict=True):
        old = {e["_id"]: describe(e) for e in old_day.get("exercises") or []}
        kept = {e["_id"] for e in new_day.get("exercises") or []}
        out += [f"{new_day.get('name')}: remove {name} {target}" for i, (name, target) in old.items() if i not in kept]
        for entry in new_day.get("exercises") or []:
            name, target = describe(entry)
            if entry["_id"] not in old:
                out.append(f"{new_day.get('name')}: add {name} {target}")
            elif old[entry["_id"]] != (name, target):
                old_name, old_target = old[entry["_id"]]
                label = name if old_name == name else f"{old_name} -> {name}"
                out.append(f"{new_day.get('name')}: {label} {old_target} -> {target}")
    return out


def _edit(op: str, day: str, exercise: str, plan: str, apply: bool, **kw) -> dict:
    """Preview or save one change to a plan (the active one unless `plan` names another)."""
    exercises = _exercises()
    all_plans = _docs("plan", refresh=True)
    found = (
        [p for p in all_plans if norm(p.get("name") or "") == norm(plan)]
        if plan
        else [p for p in all_plans if p.get("active")]
    )
    if len(found) != 1:
        names = ", ".join(p.get("name") or "?" for p in all_plans)
        raise plans.PlanEditError(
            f"No plan {plan!r}. Plans: {names}" if plan else "No active plan. Name one with `plan`."
        )
    current = found[0]
    edited = plans.edit(current, exercises, op, day, exercise, _usage(), **kw)
    result = {"plan": current.get("name"), "change": _plan_diff(current, edited, exercises), "saved": False}
    if not apply:
        result["next_step"] = (
            "Show this change to the user. If they approve, call again with the same arguments and apply=true."
        )
        return result
    config.write_private(config.BACKUP_DIR / f"{current['_id']}-{current['_rev']}.json", current)
    couch.put(edited)
    result["saved"] = True
    result["next_step"] = "Saved. The app shows it after its next sync (reopen the app if needed)."
    return result


def set_plan_targets(
    day: str, exercise: str, sets: int | None = None, reps: int | None = None, plan: str = "", apply: bool = False
) -> dict:
    """Change the target sets and/or reps of one exercise in the training plan.

    Example: day="Day 1", exercise="Bench Press", sets=4, reps=6. `day` is a day name or number.
    Returns a preview; nothing is saved unless apply=true (only after the user approved the preview).
    """
    return _edit("set", day, exercise, plan, apply, sets=sets, reps=reps)


def add_plan_exercise(
    day: str, exercise: str, sets: int, reps: int, after: str = "", plan: str = "", apply: bool = False
) -> dict:
    """Add one exercise to a day of the training plan, at the end or right after the exercise named in `after`.

    Example: day="Day 2", exercise="Seated Leg Curls (machine)", sets=3, reps=10, after="Deadlifts".
    Returns a preview; nothing is saved unless apply=true (only after the user approved the preview).
    """
    return _edit("add", day, exercise, plan, apply, sets=sets, reps=reps, after=after)


def swap_plan_exercise(
    day: str,
    exercise: str,
    new_exercise: str,
    sets: int | None = None,
    reps: int | None = None,
    plan: str = "",
    apply: bool = False,
) -> dict:
    """Replace one exercise on a day of the training plan with another, keeping its targets unless sets/reps are given.

    Example: day="Day 2", exercise="Overhead Triceps Extensions", new_exercise="Dips", sets=3, reps=8.
    Returns a preview; nothing is saved unless apply=true (only after the user approved the preview).
    """
    return _edit("swap", day, exercise, plan, apply, new_exercise=new_exercise, sets=sets, reps=reps)


def remove_plan_exercise(day: str, exercise: str, plan: str = "", apply: bool = False) -> dict:
    """Take one exercise off a day of the training plan. Past workouts with it stay in the history.

    Example: day="Day 2", exercise="Overhead Triceps Extensions".
    Returns a preview; nothing is saved unless apply=true (only after the user approved the preview).
    """
    return _edit("remove", day, exercise, plan, apply)


if config.ALLOW_WRITES:
    PLAN_WRITE = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
    )
    for tool in (set_plan_targets, add_plan_exercise, swap_plan_exercise, remove_plan_exercise):
        mcp.tool(annotations=PLAN_WRITE)(tool)


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
    print(f"Logged in. Token saved to {config.TOKEN_FILE}.")
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
