"""Application dependency resolution (pure logic).

Produces a deterministic topological installation order for the selected
applications plus their transitive dependencies, detecting missing entries,
disabled dependencies and circular graphs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class AppNode:
    id: uuid.UUID
    name: str
    enabled: bool
    dependency_ids: frozenset[uuid.UUID]


def resolve_install_order(
    selected_ids: list[uuid.UUID],
    catalog: dict[uuid.UUID, AppNode],
) -> tuple[list[AppNode], list[str]]:
    """Return ``(ordered_nodes, errors)``.

    The ordered list contains exactly the selected apps plus every transitive
    dependency, dependencies first. When errors are non-empty the order is
    undefined and provisioning must be blocked.
    """
    errors: list[str] = []
    required: dict[uuid.UUID, AppNode] = {}

    # Iterative DFS collecting the transitive closure.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[uuid.UUID, int] = {}
    chain: list[uuid.UUID] = []

    def visit(app_id: uuid.UUID) -> None:
        state = color.get(app_id, WHITE)
        if state == BLACK:
            return
        if state == GRAY:
            cycle_start = chain.index(app_id)
            cycle_names = [catalog[cid].name if cid in catalog else str(cid) for cid in chain[cycle_start:]]
            errors.append("Circular dependency detected: " + " -> ".join(cycle_names))
            return
        node = catalog.get(app_id)
        if node is None:
            errors.append(f"A selected application depends on application '{app_id}', which does not exist.")
            color[app_id] = BLACK
            return
        color[app_id] = GRAY
        chain.append(app_id)
        for dep_id in sorted(node.dependency_ids, key=str):
            visit(dep_id)
        chain.pop()
        color[app_id] = BLACK
        required[app_id] = node

    for selected in selected_ids:
        visit(selected)

    for app_id, node in required.items():
        if not node.enabled:
            errors.append(f"Application '{node.name}' is disabled in the catalog and cannot be installed.")

    if errors:
        return [], errors

    # Kahn topological sort restricted to the required subgraph.
    indegree: dict[uuid.UUID, int] = {app_id: 0 for app_id in required}
    edges_out: dict[uuid.UUID, list[uuid.UUID]] = {app_id: [] for app_id in required}
    for app_id, node in required.items():
        for dep_id in node.dependency_ids:
            if dep_id in required:
                edges_out[dep_id].append(app_id)
                indegree[app_id] += 1

    ready = sorted([app_id for app_id, degree in indegree.items() if degree == 0], key=lambda a: required[a].name)
    ordered_ids: list[uuid.UUID] = []
    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for dependent in edges_out[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
        ready.sort(key=lambda a: required[a].name)

    if len(ordered_ids) != len(required):  # pragma: no cover - guarded by cycle detection above
        errors.append("Dependencies could not be ordered (internal consistency error).")
        return [], errors

    return [required[app_id] for app_id in ordered_ids], []
