"""The audit sink — persists a run's trace and envelope to a run directory.

Every ceremony leaves a re-readable log on disk (the framework's auditability
discipline). Two security properties, both part of the M0 exit criterion:

* Everything written is passed through :func:`rapier.secrets.redact_obj` first.
* Files and directories are created owner-only (0600 / 0700).
"""
from __future__ import annotations

import json
import os
import re
import time

from .envelope import Envelope
from .secrets import redact_obj


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return text[:40] or "run"


class Ledger:
    def __init__(self, root: str, run_slug: str = "run"):
        stamp = time.strftime("%Y%m%d%H%M%S")
        self.run_dir = os.path.join(root, f"{stamp}-{_slug(run_slug)}")
        os.makedirs(self.run_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.run_dir, 0o700)
        except OSError:  # pragma: no cover - platform dependent
            pass
        self.ledger_path = os.path.join(self.run_dir, "ledger.jsonl")

    def _open_owner_only(self, path: str, append: bool) -> int:
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
        return os.open(path, flags, 0o600)

    def _write(self, name: str, obj) -> str:
        path = os.path.join(self.run_dir, name)
        payload = json.dumps(redact_obj(obj), indent=2, default=str)
        with os.fdopen(self._open_owner_only(path, append=False), "w") as fh:
            fh.write(payload)
        return path

    def record_stage(self, stage_name: str, entry: dict) -> None:
        line = json.dumps(redact_obj({"stage": stage_name, **entry}), default=str)
        with os.fdopen(self._open_owner_only(self.ledger_path, append=True), "a") as fh:
            fh.write(line + "\n")

    def write_text(self, name: str, text: str) -> str:
        """Write a redacted text/markdown artifact (owner-only) to the run dir."""
        path = os.path.join(self.run_dir, name)
        with os.fdopen(self._open_owner_only(path, append=False), "w") as fh:
            fh.write(redact_obj(text))
        return path

    def write_json(self, name: str, obj) -> str:
        """Write a redacted JSON artifact (owner-only) to the run dir."""
        return self._write(name, obj)

    def record_transcript(self, event: dict) -> None:
        """Append one verbatim model-call record (redacted) to transcript.jsonl."""
        path = os.path.join(self.run_dir, "transcript.jsonl")
        line = json.dumps(redact_obj(event), default=str)
        with os.fdopen(self._open_owner_only(path, append=True), "a") as fh:
            fh.write(line + "\n")

    def persist_envelope(self, env: Envelope) -> str:
        return self._write("envelope.json", env.to_dict())
