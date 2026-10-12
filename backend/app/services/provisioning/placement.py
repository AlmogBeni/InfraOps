"""Automatic storage placement.

When a request leaves storage on automatic, the VM goes to the first datastore
of the administrators' preferred list (Settings → Preferred datastores) that the
compute target can reach and that has room for every disk. Without a list the
datastore with the most free space is used, which in many estates is a slow
capacity tier; preflight warns about that.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.schemas.infrastructure import DatastoreOut


@dataclass(frozen=True)
class DatastoreChoice:
    datastore: DatastoreOut | None
    # Why this datastore was chosen, or why none could be.
    detail: str
    preferred_configured: bool


def normalize_preferred(names: Iterable[object] | None) -> list[str]:
    """Trimmed names in priority order, without blanks or case-insensitive duplicates."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in names or []:
        name = str(raw).strip()
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def choose_automatic_datastore(
    datastores: Iterable[DatastoreOut], preferred: Iterable[object] | None, required_gb: float
) -> DatastoreChoice:
    """Pick the datastore for a request that leaves storage on automatic."""
    datastores = list(datastores)
    ordered = normalize_preferred(preferred)
    if not ordered:
        fitting = [entry for entry in datastores if entry.accessible and entry.free_gb >= required_gb]
        best = max(fitting, key=lambda entry: entry.free_gb, default=None)
        if best is None:
            largest = max((entry.free_gb for entry in datastores if entry.accessible), default=0.0)
            return DatastoreChoice(
                None,
                f"No accessible datastore has {required_gb:.0f} GB free (largest: {largest:.0f} GB).",
                False,
            )
        return DatastoreChoice(
            best,
            f"{best.name} ({best.free_gb:.0f} GB free): the datastore with the most free space, "
            "because no preferred datastores are configured",
            False,
        )

    by_name = {entry.name.casefold(): entry for entry in datastores}
    problems: list[str] = []
    for rank, name in enumerate(ordered, start=1):
        entry = by_name.get(name.casefold())
        if entry is None:
            problems.append(f"{name} is not connected to this compute target")
        elif not entry.accessible:
            problems.append(f"{entry.name} is not accessible")
        elif entry.free_gb < required_gb:
            problems.append(f"{entry.name} has {entry.free_gb:.0f} GB free")
        else:
            return DatastoreChoice(
                entry,
                f"{entry.name} (preferred datastore {rank} of {len(ordered)}, {entry.free_gb:.0f} GB free)",
                True,
            )
    return DatastoreChoice(
        None,
        f"None of the preferred datastores can take the {required_gb:.0f} GB this VM needs: "
        + "; ".join(problems) + ".",
        True,
    )
