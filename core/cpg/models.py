from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import time


class EdgeType(str, Enum):
    CALLS      = "calls"
    CALLED_BY  = "called_by"
    IMPORTS    = "imports"
    INHERITS   = "inherits"
    DATA_FLOW  = "data_flow"


@dataclass
class CPGNode:
    node_id:       str
    file_path:     str
    function_name: str
    class_name:    Optional[str]
    signature:     str
    return_type:   str
    decorators:    List[str]
    raises:        List[str]
    body_hash:     str
    start_line:    int
    end_line:      int
    is_stale:      bool  = False
    last_modified: float = 0.0
    # Written back after a verified successful edit — the Knowledge Item link
    summary:       Optional[str] = None

    @property
    def loc(self) -> int:
        return self.end_line - self.start_line + 1

    def mark_stale(self):
        self.is_stale = True

    def resolve(self, new_hash: str):
        self.body_hash     = new_hash
        self.is_stale      = False
        self.last_modified = time.time()

    def to_dict(self) -> dict:
        return {
            "node_id":       self.node_id,
            "file_path":     self.file_path,
            "function_name": self.function_name,
            "class_name":    self.class_name,
            "signature":     self.signature,
            "return_type":   self.return_type,
            "decorators":    self.decorators,
            "raises":        self.raises,
            "body_hash":     self.body_hash,
            "start_line":    self.start_line,
            "end_line":      self.end_line,
            "is_stale":      self.is_stale,
            "last_modified": self.last_modified,
            "summary":       self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CPGNode":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class CPGEdge:
    source_id:      str
    target_id:      str
    edge_type:      EdgeType
    call_frequency: int  = 1
    metadata:       Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_id":      self.source_id,
            "target_id":      self.target_id,
            "edge_type":      self.edge_type,
            "call_frequency": self.call_frequency,
            "metadata":       self.metadata,
        }