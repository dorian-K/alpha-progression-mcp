"""Forgiving input matching, so imprecise names from an assistant still resolve, or fail with suggestions."""

import difflib
import re
from datetime import date

from .analysis import MUSCLE_GROUPS


class InputError(ValueError):
    pass


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_exercise(query: str, exercises: dict[str, dict], usage: dict[str, int] | None = None) -> set[str]:
    """Exercise ids for an id or name: exact match, else the first unique partial match among, in order,
    names starting with the query, then names containing all its words (each among used exercises first).
    If several names match, one done at least 3x as often as the next wins."""
    if query in exercises:
        return {query}
    q = norm(query)
    names = {i: norm(e.get("name") or "") for i, e in exercises.items()}
    exact = {i for i, n in names.items() if n == q}
    if exact:
        return exact
    usage = usage or {}
    preferred = [i for i in names if usage.get(i)]
    tiers = [
        (pool, test)
        for pool in (preferred, list(names))
        for test in (lambda n: n.startswith(q), lambda n: all(w in n for w in q.split()))
    ]
    for pool, test in tiers:
        hits = [i for i in pool if q and test(names[i])]
        hit_names = {exercises[i].get("name") for i in hits}
        if len(hit_names) == 1:
            return set(hits)
        if len(hit_names) > 1:
            by_use = sorted(hits, key=lambda i: usage.get(i, 0), reverse=True)
            if usage.get(by_use[0], 0) >= 3 * max(1, usage.get(by_use[1], 0)):
                return {by_use[0]}
            options = sorted(hit_names, key=len)[:6]
            raise InputError(f"{query!r} matches several exercises: {', '.join(options)}. Use the exact name.")
    pool_names = sorted({exercises[i].get("name") for i in (preferred or names) if exercises[i].get("name")})
    close = difflib.get_close_matches(query, pool_names, n=5, cutoff=0.4)
    hint = f" Did you mean: {', '.join(close)}?" if close else " Use find_exercises to look it up."
    raise InputError(f"No exercise matches {query!r}.{hint}")


def match_day(days: list[dict], query: str) -> dict:
    """A plan day by name ("Day 1", "day1") or number ("1")."""
    q = norm(str(query))
    for day in days:
        if norm(day.get("name") or "") == q:
            return day
    number = re.fullmatch(r"(?:day ?)?(\d+)", q)
    if number and 1 <= int(number[1]) <= len(days):
        return days[int(number[1]) - 1]
    raise InputError(f"No day {query!r}. Days in this plan: {', '.join(d.get('name') or '?' for d in days)}")


def parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise InputError(f"{field} must be a date like 2026-10-28, got {value!r}.") from None


def check_muscle(muscle: str) -> str:
    """Validate a muscle or muscle-group filter; empty means no filter."""
    m = muscle.strip().lower()
    valid = set(MUSCLE_GROUPS) | set(MUSCLE_GROUPS.values())
    if m and m not in valid:
        raise InputError(
            f"Unknown muscle {muscle!r}. Muscles: {', '.join(sorted(MUSCLE_GROUPS))}. "
            f"Groups: {', '.join(sorted(set(MUSCLE_GROUPS.values())))}."
        )
    return m
