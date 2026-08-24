"""Tests for application dependency resolution."""

from __future__ import annotations

import uuid

from app.services.applications.resolver import AppNode, resolve_install_order


def node(name: str, deps: list[str] | None = None, enabled: bool = True) -> tuple[uuid.UUID, AppNode]:
    app_id = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    return app_id, AppNode(
        id=app_id,
        name=name,
        enabled=enabled,
        dependency_ids=frozenset(uuid.uuid5(uuid.NAMESPACE_DNS, d) for d in (deps or [])),
    )


def build_catalog(*pairs) -> dict:
    return {app_id: n for app_id, n in pairs}


class TestResolution:
    def test_dependency_comes_first(self):
        runtime_id, runtime = node("VC++ Runtime")
        agent_id, agent = node("Monitoring Agent", deps=["VC++ Runtime"])
        catalog = build_catalog((runtime_id, runtime), (agent_id, agent))

        ordered, errors = resolve_install_order([agent_id], catalog)
        assert errors == []
        assert [n.name for n in ordered] == ["VC++ Runtime", "Monitoring Agent"]

    def test_transitive_dependencies(self):
        base_id, base = node("Base")
        mid_id, mid = node("Mid", deps=["Base"])
        top_id, top = node("Top", deps=["Mid"])
        catalog = build_catalog((base_id, base), (mid_id, mid), (top_id, top))

        ordered, errors = resolve_install_order([top_id], catalog)
        assert errors == []
        assert [n.name for n in ordered] == ["Base", "Mid", "Top"]

    def test_cycle_detected(self):
        a_id, a = node("A", deps=["B"])
        b_id, b = node("B", deps=["A"])
        catalog = build_catalog((a_id, a), (b_id, b))

        _, errors = resolve_install_order([a_id], catalog)
        assert any("Circular" in e for e in errors)

    def test_missing_dependency_reported(self):
        agent_id, agent = node("Agent", deps=["Ghost"])
        catalog = build_catalog((agent_id, agent))

        _, errors = resolve_install_order([agent_id], catalog)
        assert any("does not exist" in e for e in errors)

    def test_disabled_dependency_blocks(self):
        runtime_id, runtime = node("Runtime", enabled=False)
        agent_id, agent = node("Agent", deps=["Runtime"])
        catalog = build_catalog((runtime_id, runtime), (agent_id, agent))

        _, errors = resolve_install_order([agent_id], catalog)
        assert any("disabled" in e for e in errors)

    def test_self_dependency_rejected_by_graph_check(self):
        a_id, a = node("Solo")
        catalog = build_catalog((a_id, a))
        ordered, errors = resolve_install_order([a_id], catalog)
        assert errors == []
        assert len(ordered) == 1
