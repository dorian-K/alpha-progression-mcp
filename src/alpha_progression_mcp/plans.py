"""Plan edits limited to what the free version of the app can do: one sets/reps target per exercise.

Pro-only plan features (RIR targets and tracking, deloads, periodisation, multi-week targets, generated plans)
are never written, and plans that use them are refused. Workout history is never touched.
"""

import copy
import time

from .matching import InputError, match_day, match_exercise

PRO_PLAN_FLAGS = ("trackRir", "deload", "periodisation", "planGenerator")
LIMITS = {"sets": (1, 10), "reps": (1, 100)}


class PlanEditError(InputError):
    pass


def check_free(plan: dict) -> None:
    used = [f for f in PRO_PLAN_FLAGS if plan.get(f)]
    for day in plan.get("days") or []:
        for ex in day.get("exercises") or []:
            weeks = ex.get("weeks") or []
            if len(weeks) > 1 or any(w.get("rir") for w in weeks):
                used.append(f"{day.get('name')}: per-week or RIR targets")
    if used:
        raise PlanEditError(f"This plan uses Pro features ({', '.join(used)}). Edit it in the app instead.")


def _find(day: dict, query: str, exercises: dict[str, dict]) -> dict:
    """The entry for an exercise already on this day."""
    on_day = {e.get("exerciseId") for e in day.get("exercises") or []}
    try:
        ids = match_exercise(query, {i: exercises.get(i, {"name": i}) for i in on_day})
    except InputError:
        names = ", ".join(exercises.get(i, {}).get("name") or i for i in on_day)
        raise PlanEditError(f"{query!r} is not on {day.get('name')}. Exercises there: {names}") from None
    return next(e for e in day["exercises"] if e.get("exerciseId") in ids)


def _set_target(entry: dict, sets: int | None, reps: int | None) -> None:
    week = entry.setdefault("weeks", [{}])[0]
    for key, value in (("sets", sets), ("reps", reps)):
        if value is None:
            continue
        low, high = LIMITS[key]
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise PlanEditError(f"{key} must be a whole number from {low} to {high}, got {value!r}.")
        week[key] = value


def edit(
    plan: dict, exercises: dict[str, dict], op: str, day: str, exercise: str, usage: dict | None = None, **kw
) -> dict:
    """Return an edited copy of `plan` with one change applied.

    op "set": change `sets`/`reps` of `exercise` on `day`.
    op "add": add `exercise` to `day` with `sets` and `reps`, at the end or right after `after`.
    op "swap": replace `exercise` on `day` with `new_exercise`, keeping or changing its targets.
    op "remove": take `exercise` off `day`. Past workouts with it stay in the history.
    """
    check_free(plan)
    new = copy.deepcopy(plan)
    target_day = match_day(new.get("days") or [], day)
    sets, reps = kw.get("sets"), kw.get("reps")
    if op == "set":
        if sets is None and reps is None:
            raise PlanEditError("Give sets, reps or both.")
        _set_target(_find(target_day, exercise, exercises), sets, reps)
    elif op == "add":
        ex_id = sorted(match_exercise(exercise, exercises, usage))[0]
        entries = target_day.setdefault("exercises", [])
        if any(e.get("exerciseId") == ex_id for e in entries):
            raise PlanEditError(
                f"{exercises[ex_id]['name']} is already on {target_day.get('name')}; change its sets/reps instead."
            )
        if sets is None or reps is None:
            raise PlanEditError("Adding an exercise needs both sets and reps.")
        entry = {"_id": str(int(time.time() * 1000)), "exerciseId": ex_id, "weeks": [{}]}
        _set_target(entry, sets, reps)
        position = entries.index(_find(target_day, kw["after"], exercises)) + 1 if kw.get("after") else len(entries)
        entries.insert(position, entry)
    elif op == "swap":
        entry = _find(target_day, exercise, exercises)
        entry["exerciseId"] = sorted(match_exercise(kw["new_exercise"], exercises, usage))[0]
        _set_target(entry, sets, reps)
    elif op == "remove":
        entries = target_day["exercises"]
        entries.remove(_find(target_day, exercise, exercises))
    else:
        raise PlanEditError(f"Unknown change {op!r}.")
    new["lastChanged"] = int(time.time() * 1000)
    return new
