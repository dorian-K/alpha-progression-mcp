"""Training metrics computed from raw workout documents. Pure functions, no I/O."""

import html
from collections import defaultdict
from datetime import date, datetime, timedelta

LB_TO_KG = 0.45359237
WORKING_SET_RATIO = 0.75  # a weighted set below 75% of the session's heaviest set for that exercise is a warm-up
SECONDARY_WEIGHT = 0.5  # fractional counting: a set counts half for a secondary muscle (Pelland et al.)
SECONDARY_COUNTED = 2  # built-in exercises list stabilisers too; only the first two secondaries count
MAX_E1RM_REPS = 12  # rep-based 1RM estimates get unreliable above this

MUSCLE_GROUPS = {
    "chest": "chest",
    "lats": "back",
    "upper-back": "back",
    "traps": "back",
    "lower-back": "back",
    "front-delts": "shoulders",
    "side-delts": "shoulders",
    "rear-delts": "shoulders",
    "biceps": "arms",
    "triceps": "arms",
    "forearms": "arms",
    "quads": "legs",
    "hams": "legs",
    "glutes": "legs",
    "calves": "legs",
    "abductors": "legs",
    "adductors": "legs",
    "abs": "core",
}


def to_time(ms: int | None) -> datetime | None:
    """The app stores times as epoch milliseconds."""
    return datetime.fromtimestamp(ms / 1000).astimezone() if ms else None


def iso(ms: int | None) -> str | None:
    t = to_time(ms)
    return t.isoformat(timespec="minutes") if t else None


def clean_name(name: str | None) -> str | None:
    """Custom exercise names are stored HTML-escaped (e.g. "Child&apos;s pose")."""
    return html.unescape(name) if name else name


def kg(s: dict) -> float | None:
    w = s.get("weight")
    if not isinstance(w, (int, float)):
        return None
    return w * LB_TO_KG if s.get("weightUnit") in ("lb", "lbs") else w


def performed(sets: list[dict] | None) -> list[dict]:
    """Sets that were actually done. Each finished set has a `completed` timestamp; planned ones don't."""
    return [s for s in sets or [] if s.get("completed")]


def is_mobility(name: str | None, sets: list[dict]) -> bool:
    """Stretches and mobility drills: named so, or only unweighted holds logged as 25+ reps (seconds)."""
    if name and "stretch" in name.lower():
        return True
    return bool(sets) and all(not kg(s) and (s.get("reps") or 0) >= 25 for s in sets)


def working_sets(sets: list[dict]) -> list[dict]:
    """Performed sets minus warm-ups. Unweighted sets (bodyweight, holds) always count."""
    top = max((kg(s) or 0 for s in sets), default=0)
    return [s for s in sets if not kg(s) or not top or kg(s) >= WORKING_SET_RATIO * top]


def e1rm(s: dict) -> float | None:
    """Estimated one-rep max (Epley). Logged RIR is added to the reps, as reps to failure."""
    w, reps = kg(s), s.get("reps")
    if not w or not reps:
        return None
    rir = s.get("rir") if isinstance(s.get("rir"), (int, float)) and not isinstance(s.get("rir"), bool) else 0
    to_failure = reps + rir
    return round(w * (1 + to_failure / 30), 1) if to_failure <= MAX_E1RM_REPS else None


def top_set(sets: list[dict]) -> dict | None:
    """Heaviest set (ties broken by reps); for unweighted exercises the set with most reps."""
    if not sets:
        return None
    s = max(sets, key=lambda s: (kg(s) or 0, s.get("reps") or 0, s.get("duration") or 0))
    out = {"weight_kg": round(kg(s), 2) if kg(s) else None, "reps": s.get("reps")}
    if s.get("duration"):
        out["duration_s"] = s["duration"]
    if isinstance(s.get("rir"), (int, float)) and not isinstance(s.get("rir"), bool):
        out["rir"] = s["rir"]
    return out


def volume(sets: list[dict]) -> int:
    return round(sum((kg(s) or 0) * (s.get("reps") or 0) for s in sets))


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def exercise_rows(workout: dict, exercises: dict[str, dict]) -> list[dict]:
    """Per exercise in a workout: identity, muscles and the computed set metrics."""
    rows = []
    for ex in workout.get("exercises") or []:
        info = exercises.get(ex.get("exerciseId"), {})
        name = clean_name(info.get("name")) or ex.get("exerciseId")
        sets = performed(ex.get("sets"))
        work = working_sets(sets)
        rows.append(
            {
                "id": ex.get("exerciseId"),
                "name": name,
                "muscle": info.get("muscle"),
                "secondary": info.get("secondary_counted", []),
                "mobility": is_mobility(name, sets),
                "planned": len(ex.get("sets") or []),
                "sets": sets,
                "working": work,
                "e1rm": max((v for v in map(e1rm, work) if v), default=None),
            }
        )
    return rows


def muscle_sets(rows: list[dict]) -> dict[str, float]:
    """Fractional working sets per muscle: 1 per set for the target muscle, 0.5 for counted secondaries."""
    out: dict[str, float] = defaultdict(float)
    for r in rows:
        if r["mobility"] or not r["muscle"]:
            continue
        n = len(r["working"])
        out[r["muscle"]] += n
        for m in r["secondary"]:
            if m != r["muscle"]:
                out[m] += SECONDARY_WEIGHT * n
    return out
