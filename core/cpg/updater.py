"""
Incremental CPG Updater — triggered by FSWatcher on every file save.
Re-parses only the changed file, updates affected nodes, marks caller
edges stale. Never rebuilds the full graph — O(changed_file) not O(project).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import List, Set

import networkx as nx

from .builder import CPGBuilder
from .models import CPGNode, EdgeType
from .language_registry import is_supported


class CPGUpdater:
    def __init__(self, builder: CPGBuilder):
        self.builder = builder

    def on_file_changed(self, abs_path: str) -> List[str]:
        """
        Called by FSWatcher when a supported source file is saved.
        Returns list of node_ids that were updated or marked stale.
        """
        path    = Path(abs_path).resolve()
        rel     = str(path.relative_to(self.builder.project_root.resolve()))
        changed: List[str] = []

        if not path.exists() or not is_supported(str(path)):
            return changed

        # 1. Re-parse the changed file into a temp builder
        tmp = CPGBuilder(str(self.builder.project_root))
        try:
            tmp._parse_file(path)
            tmp._resolve_calls(path)
        except Exception:
            return changed

        # 2. Find existing nodes for this file
        old_ids: Set[str] = {
            nid for nid, n in self.builder.nodes.items()
            if n.file_path == rel
        }
        new_ids: Set[str] = set(tmp.nodes.keys())

        # 3. Update changed nodes
        for nid in new_ids:
            new_node = tmp.nodes[nid]
            if nid in self.builder.nodes:
                old_node = self.builder.nodes[nid]
                if old_node.body_hash != new_node.body_hash:
                    # Body changed — update node, mark it and its callers stale
                    new_node.mark_stale()
                    self.builder.nodes[nid] = new_node
                    self.builder.graph.add_node(nid, **new_node.to_dict())
                    self.builder.graph.nodes[nid]["is_stale"] = True
                    self._mark_callers_stale(nid)
                    changed.append(nid)
            else:
                # New function appeared
                self.builder.nodes[nid] = new_node
                self.builder.graph.add_node(nid, **new_node.to_dict())
                changed.append(nid)

        # 4. Remove deleted functions
        for nid in old_ids - new_ids:
            self.builder.graph.remove_node(nid)
            del self.builder.nodes[nid]
            changed.append(nid)

        # 5. Re-add call edges from the changed file
        for u, v, data in list(self.builder.graph.edges(data=True)):
            if self.builder.nodes.get(u, CPGNode.__new__(CPGNode)).file_path == rel:
                self.builder.graph.remove_edge(u, v)

        for u, v, data in tmp.graph.edges(data=True):
            if u in self.builder.nodes and v in self.builder.nodes:
                self.builder.graph.add_edge(u, v, **data)

        return changed

    def _mark_callers_stale(self, node_id: str):
        """Mark all functions that call node_id as having a stale dependency."""
        for source, _ in self.builder.graph.in_edges(node_id):
            if source in self.builder.nodes:
                self.builder.nodes[source].mark_stale()
                self.builder.graph.nodes[source]["is_stale"] = True

    def resolve_staleness(self, node_id: str):
        """Call this after a node is successfully edited and verified."""
        if node_id in self.builder.nodes:
            path     = self.builder.project_root / self.builder.nodes[node_id].file_path
            src_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            self.builder.nodes[node_id].resolve(src_hash)
            self.builder.graph.nodes[node_id]["is_stale"] = False
            self.builder.graph.nodes[node_id]["body_hash"] = src_hash