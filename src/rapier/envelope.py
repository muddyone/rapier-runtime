"""The Envelope — the single typed state object that flows through the pipeline.

Every stage reads and writes one Envelope. Nothing else is passed between
stages. Get this contract right and stages become independently swappable:
a stage is just ``run(envelope) -> envelope``.

The Envelope carries the whole ceremony: the request in, the Proposer's
evolving option space, the committed option, the Resolver's recommendation and
trust rider, the accumulated grounding artifacts, and an append-only trace that
feeds the ledger/audit sink.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TraceEntry:
    """One append-only record of what a stage did. Feeds the audit ledger."""

    stage: str
    kind: str
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Artifact:
    """A checkable grounding claim raised during the ceremony.

    ``ref`` is the bare checkable token (a CWE id, a DOI, a ``path:line``, a
    ``#3`` pack-fact pointer, a URL). ``verdict`` is set by the verification
    service in later milestones.
    """

    ref: str
    claim: str
    load_bearing: bool = False
    verdict: str = "unchecked"  # unchecked | verified | refuted | unverifiable


@dataclass
class Envelope:
    """The state passed through every stage of a Rapier pipeline."""

    request: str
    options: list[str] = field(default_factory=list)
    committed: str | None = None
    recommendation: str | None = None
    trust_rider: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    verdict: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def add_trace(self, stage: str, kind: str, summary: str, **data: Any) -> "Envelope":
        """Append an audit trace entry and return self (for chaining)."""
        self.trace.append(TraceEntry(stage=stage, kind=kind, summary=summary, data=data))
        return self

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict view for serialization (redacted before it is persisted)."""
        return asdict(self)
