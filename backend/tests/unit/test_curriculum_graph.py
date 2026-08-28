"""
Unit tests for prerequisite-graph cycle detection and resolution. Pure
functions, no I/O, no LLM.
"""
import uuid

import pytest

from app.modules.curriculum.graph import (
    ProposedEdge,
    find_cycle,
    is_acyclic,
    resolve_cycles,
)

A, B, C, D = (uuid.uuid4() for _ in range(4))


def edge(prereq, dep, strength="HARD", confidence=0.8):
    return ProposedEdge(prerequisite_id=prereq, dependent_id=dep, strength=strength, confidence=confidence)


class TestCycleDetection:
    def test_acyclic_graph_reports_no_cycle(self):
        edges = [edge(A, B), edge(B, C)]
        assert is_acyclic(edges)
        assert find_cycle(edges) is None

    def test_three_node_hard_cycle_is_detected(self):
        """A -> B -> C -> A, all hard edges."""
        edges = [edge(A, B), edge(B, C), edge(C, A)]
        assert not is_acyclic(edges)
        cycle = find_cycle(edges)
        assert cycle is not None
        assert len(cycle) == 3

    def test_self_loop_is_a_cycle(self):
        assert not is_acyclic([edge(A, A)])

    def test_two_node_mutual_cycle_is_detected(self):
        assert not is_acyclic([edge(A, B), edge(B, A)])

    def test_soft_only_cycle_is_still_detected(self):
        """
        frozen-scope.md draws no acyclicity exemption for soft edges: a
        prerequisite cycle is nonsensical regardless of whether it gates
        access or only influences a score.
        """
        edges = [edge(A, B, strength="SOFT"), edge(B, C, strength="SOFT"), edge(C, A, strength="SOFT")]
        assert not is_acyclic(edges)

    def test_mixed_hard_and_soft_cycle_is_detected(self):
        edges = [edge(A, B, strength="HARD"), edge(B, C, strength="SOFT"), edge(C, A, strength="HARD")]
        assert not is_acyclic(edges)

    def test_disconnected_acyclic_components_report_no_cycle(self):
        edges = [edge(A, B), edge(C, D)]
        assert is_acyclic(edges)

    def test_cycle_in_one_of_several_components_is_still_found(self):
        edges = [edge(A, B), edge(B, C), edge(C, B)]  # B<->C cycle, A feeds in
        assert not is_acyclic(edges)


class TestCycleResolution:
    def test_resolving_a_cycle_yields_an_acyclic_result(self):
        """The actual acceptance criterion: run acyclicity against the
        *stored* (post-resolution) result, not just trust the fix worked."""
        edges = [edge(A, B, confidence=0.9), edge(B, C, confidence=0.8), edge(C, A, confidence=0.3)]
        resolved, dropped = resolve_cycles(edges)
        assert is_acyclic(resolved)

    def test_the_lowest_confidence_edge_in_the_cycle_is_dropped(self):
        edges = [edge(A, B, confidence=0.9), edge(B, C, confidence=0.8), edge(C, A, confidence=0.3)]
        resolved, dropped = resolve_cycles(edges)
        assert dropped == [edge(C, A, confidence=0.3)]
        assert edge(A, B, confidence=0.9) in resolved
        assert edge(B, C, confidence=0.8) in resolved

    def test_soft_cycles_are_resolved_the_same_way_as_hard(self):
        edges = [
            edge(A, B, strength="SOFT", confidence=0.7),
            edge(B, C, strength="SOFT", confidence=0.2),
            edge(C, A, strength="SOFT", confidence=0.9),
        ]
        resolved, dropped = resolve_cycles(edges)
        assert is_acyclic(resolved)
        assert dropped[0].confidence == 0.2

    def test_multiple_independent_cycles_are_all_resolved(self):
        E, F = uuid.uuid4(), uuid.uuid4()
        edges = [
            edge(A, B, confidence=0.9), edge(B, A, confidence=0.1),  # cycle 1
            edge(C, D, confidence=0.9), edge(D, C, confidence=0.2),  # cycle 2
            edge(E, F),  # unrelated, acyclic
        ]
        resolved, dropped = resolve_cycles(edges)
        assert is_acyclic(resolved)
        assert len(dropped) == 2

    def test_non_cyclic_edges_are_never_dropped(self):
        edges = [edge(A, B, confidence=0.9), edge(B, C, confidence=0.8), edge(C, A, confidence=0.3)]
        _, dropped = resolve_cycles(edges)
        untouched = [e for e in edges if e not in dropped]
        assert edge(A, B, confidence=0.9) in untouched

    def test_already_acyclic_graph_drops_nothing(self):
        edges = [edge(A, B), edge(B, C)]
        resolved, dropped = resolve_cycles(edges)
        assert resolved == edges
        assert dropped == []

    def test_resolution_of_a_larger_cycle(self):
        """A -> B -> C -> D -> A, confirm the weakest of four is dropped and
        the rest survive."""
        edges = [
            edge(A, B, confidence=0.9),
            edge(B, C, confidence=0.7),
            edge(C, D, confidence=0.1),
            edge(D, A, confidence=0.6),
        ]
        resolved, dropped = resolve_cycles(edges)
        assert is_acyclic(resolved)
        assert dropped == [edge(C, D, confidence=0.1)]
        assert len(resolved) == 3
