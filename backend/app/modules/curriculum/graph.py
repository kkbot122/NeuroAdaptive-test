"""
Prerequisite graph acyclicity: pure functions over an edge list, no
database, no model call. DFS-based rather than a networkx dependency --
cycle detection over a few hundred concepts needs nothing heavier.

The mandate's rule enforced here: an LLM proposes edges, this module decides
acyclicity deterministically. The LLM's own confidence never substitutes for
the check.
"""
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple
from uuid import UUID


@dataclass(frozen=True)
class ProposedEdge:
    prerequisite_id: UUID
    dependent_id: UUID
    strength: str  # "HARD" | "SOFT" -- not exempted from acyclicity either way
    confidence: float


def find_cycle(edges: List[ProposedEdge]) -> Optional[List[ProposedEdge]]:
    """
    Returns the edge list forming one cycle, or None if the graph is acyclic.

    Soft edges are included in the same graph as hard edges: frozen-scope.md
    draws no exemption for either, and a cycle is a cycle regardless of which
    edges in it happen to be soft.
    """
    graph: Dict[UUID, List[ProposedEdge]] = {}
    for edge in edges:
        graph.setdefault(edge.prerequisite_id, []).append(edge)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[UUID, int] = {}
    path: List[ProposedEdge] = []

    def visit(node: UUID) -> Optional[List[ProposedEdge]]:
        color[node] = GRAY
        for edge in graph.get(node, []):
            nxt = edge.dependent_id
            if color.get(nxt, WHITE) == WHITE:
                path.append(edge)
                result = visit(nxt)
                if result is not None:
                    return result
                path.pop()
            elif color.get(nxt) == GRAY:
                # Found the back-edge closing a cycle. Walk `path` to find
                # where the cycle actually starts (the tail of `path` may
                # contain nodes outside the cycle if we DFS'd through them).
                path.append(edge)
                start_index = next(
                    i for i, e in enumerate(path) if e.prerequisite_id == nxt
                )
                cycle = path[start_index:]
                path.pop()
                return cycle
        color[node] = BLACK
        return None

    all_nodes = set()
    for edge in edges:
        all_nodes.add(edge.prerequisite_id)
        all_nodes.add(edge.dependent_id)

    for node in all_nodes:
        if color.get(node, WHITE) == WHITE:
            result = visit(node)
            if result is not None:
                return result
    return None


def is_acyclic(edges: List[ProposedEdge]) -> bool:
    return find_cycle(edges) is None


def resolve_cycles(edges: List[ProposedEdge]) -> Tuple[List[ProposedEdge], List[ProposedEdge]]:
    """
    Repeatedly find and break cycles until none remain.

    Resolution: the lowest-confidence edge in each detected cycle is dropped
    from the prerequisite graph (demoted to a non-prerequisite "related"
    relationship, which this function represents simply as removal -- the
    curriculum service is what would separately record a "related concept"
    row if that relationship type is wanted; this module owns acyclicity
    only). A cycle is never left in the returned edge list.

    Returns (acyclic_edges, dropped_edges).
    """
    remaining = list(edges)
    dropped: List[ProposedEdge] = []

    # Bounded by len(edges): each iteration removes exactly one edge, so this
    # cannot loop longer than the input has edges.
    for _ in range(len(edges) + 1):
        cycle = find_cycle(remaining)
        if cycle is None:
            return remaining, dropped

        weakest = min(cycle, key=lambda e: e.confidence)
        remaining = [e for e in remaining if e != weakest]
        dropped.append(weakest)

    # Unreachable given the loop bound and that every iteration strictly
    # shrinks the edge set, but fail loudly rather than silently return a
    # possibly-cyclic result if this invariant is ever violated.
    raise RuntimeError("resolve_cycles did not converge within the expected bound")
