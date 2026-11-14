"""Operator policy: a minimum for the checks, which a caller's argument cannot lower.

Over MCP (and over the CLI, when an agent runs it) the agent whose output is
being checked chooses ``verify``. Left alone, it could switch the citation
check off. This module lets the OPERATOR set a floor that no tool argument
can get under, and reports where that floor came from and whether the caller
could have changed it.

A control counts as external only if the governed agent cannot change,
disable or get around it. Sitting outside the model is not enough. So each
source of the minimum is labelled by whether the running process could write
it:

* **Policy file** at a fixed system path (``/etc/rapier/policy.json``; on
  Windows ``%PROGRAMDATA%\\rapier\\policy.json``). The path is deliberately NOT
  configurable by environment variable: a path the caller can redirect is a
  floor the caller can remove. The file counts as out of the caller's reach
  only when this process is not root and neither owns nor can write the file
  or any directory above it (for example, root-owned and read-only to the user
  that runs Rapier). Otherwise the minimum still applies, but the report says
  it was within the caller's reach. A file that exists but can't be checked,
  read or parsed fails closed.
* **``RAPIER_MIN_VERIFY``** environment variable. Applied as a minimum too, but
  always reported as within the caller's reach: whoever launches the process
  sets its environment.

The strongest source wins. A minimum can only raise the bar, never lower it.
A policy file that exists but cannot be read or parsed fails CLOSED: the run is
refused rather than carried on without the floor the operator meant to set.

File format::

    {"min_verify": "gate"}

Order of the verify levels: ``off`` < ``gate`` <= ``round``. ``round`` runs the
citation gate after every review round, the last one included, so it checks at
least what ``gate`` checks and never counts as lowering a ``gate`` minimum.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

VERIFY_RANK = {"off": 0, "gate": 1, "round": 1}
ENV_VAR = "RAPIER_MIN_VERIFY"


def _default_policy_path() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("PROGRAMDATA") or r"C:\ProgramData"
        return os.path.join(base, "rapier", "policy.json")
    return "/etc/rapier/policy.json"


# Module-level so tests can point it elsewhere; never read from the environment.
POLICY_PATH = _default_policy_path()


class PolicyError(Exception):
    """The run is refused: the operator's policy DECIDED against it (a caller
    asked for less than the minimum, or no independent review is possible)."""


class PolicyUnreadable(PolicyError):
    """The run is stopped because the operator's policy could not be read or
    checked (fail closed). The control did not run, so it decided nothing: the
    stop holds, but it is never credited to the minimum, and the final state is
    ``defer``, not ``refuse``. Subclasses PolicyError so a caller that only
    wants "stopped or not" still catches both."""


def stop_result(exc: PolicyError, refused_by: str) -> dict:
    """The MCP result for a policy stop, attributing it only where a control decided."""
    if isinstance(exc, PolicyUnreadable):
        return {"ok": False, "error": str(exc), "final_state": "defer",
                "operator_policy": f"did-not-run: {exc}"}
    return {"ok": False, "error": str(exc), "refused_by": refused_by, "final_state": "refuse"}


def stop_message(exc: PolicyError) -> str:
    """The CLI's stderr line for a policy stop (exit code 2 either way)."""
    if isinstance(exc, PolicyUnreadable):
        return f"stopped (operator policy did not run, final state defer): {exc}"
    return f"refused: {exc}"


@dataclass(frozen=True)
class VerifyDecision:
    mode: str                 # the verify mode that will run
    set_by: str               # who chose it, e.g. "caller (MCP tool argument)"
    minimum: str | None       # the operator minimum, if any
    minimum_source: str       # e.g. "/etc/rapier/policy.json" or "RAPIER_MIN_VERIFY"
    minimum_external: bool    # True only if the running process could not change it
    require_independent: bool = False  # operator requires a second-vendor review (D-68)

    def meta(self) -> dict:
        return {
            "verify": self.mode,
            "verify_set_by": self.set_by,
            "verify_minimum": self.minimum,
            "verify_minimum_source": self.minimum_source,
            "verify_minimum_external": self.minimum_external,
            # Carried to compose, so a required review that fails AT RUN TIME
            # can't be reported as passed (verifier R6-1).
            "require_independent": self.require_independent,
        }


def _check_level(level, where: str) -> str:
    if level not in VERIFY_RANK:
        raise PolicyUnreadable(f"operator policy {where}: unknown verify level {level!r}; "
                          f"known: {sorted(VERIFY_RANK)}")
    return level


def _out_of_my_reach(path: str) -> bool:
    """True only if this process has no authority over the policy file.

    File modes alone are not enough: the owner of a file or of any directory
    above it can chmod it back, or rename the directory and put another file in
    its place. So the file and every directory up to the root must be neither
    owned by this process's user nor writable by it, and the process must not be
    root. Where ownership can't be checked (no ``geteuid``, e.g. Windows), the
    answer is False: a control is not reported as external unless it is shown
    to be."""
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    me = geteuid()
    if me == 0:
        return False
    cur = os.path.abspath(path)
    while True:
        try:
            st = os.stat(cur)
        except OSError:
            return False
        if st.st_uid == me or os.access(cur, os.W_OK):
            return False
        parent = os.path.dirname(cur)
        if parent == cur:
            return True
        cur = parent


_POLICY_KEYS = ("min_verify", "require_independent")


def _read_policy_file() -> tuple[dict, str, bool] | None:
    """The operator policy file as ``(data, path, external)``, or None when absent.
    Fails closed on anything that isn't a clean, readable policy."""
    path = POLICY_PATH
    try:
        os.stat(path)
    except FileNotFoundError:
        return None  # the only case that means "no policy": the file is not there
    except OSError as exc:
        # Can't tell whether a policy exists (e.g. its directory can't be searched).
        # An operator who locked the directory down meant a policy to apply: fail closed.
        raise PolicyUnreadable(f"operator policy file {path} could not be checked ({exc}); "
                          "refusing to run without the minimum it may set") from exc
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise PolicyUnreadable(f"operator policy file {path} could not be read ({exc}); "
                          "refusing to run without the minimum it sets") from exc
    if not isinstance(data, dict) or not any(k in data for k in _POLICY_KEYS):
        raise PolicyUnreadable(f"operator policy file {path} sets none of {list(_POLICY_KEYS)}; "
                          "refusing to run without the minimum it was meant to set")
    if "require_independent" in data and not isinstance(data["require_independent"], bool):
        raise PolicyUnreadable(f"operator policy file {path}: require_independent must be "
                          "true or false; refusing to run")
    return data, path, _out_of_my_reach(path)


def _file_minimum() -> tuple[str, str, bool] | None:
    got = _read_policy_file()
    if got is None or "min_verify" not in got[0]:
        return None
    data, path, external = got
    return _check_level(data["min_verify"], f"file {path}"), path, external


def _env_minimum() -> tuple[str, str, bool] | None:
    raw = os.environ.get(ENV_VAR)
    if raw is None or raw.strip() == "":
        return None
    return _check_level(raw.strip().lower(), f"env {ENV_VAR}"), ENV_VAR, False


def operator_minimum() -> tuple[str | None, str, bool]:
    """The effective minimum ``(level, source, external)``. With two sources the
    stronger level wins; at equal strength the external one is reported."""
    found = [m for m in (_file_minimum(), _env_minimum()) if m]
    if not found:
        return None, "", False
    found.sort(key=lambda m: (VERIFY_RANK[m[0]], m[2]), reverse=True)
    return found[0]


def decide_verify(requested: str, set_by: str) -> VerifyDecision:
    """Apply the operator minimum to a requested verify mode.

    Raises :class:`PolicyError` when ``requested`` is below the minimum. The
    request is refused, not silently raised, so the caller learns its argument
    did not take effect and why."""
    minimum, source, external = operator_minimum()
    if minimum is not None and VERIFY_RANK.get(requested, -1) < VERIFY_RANK[minimum]:
        reach = ("out of the caller's reach" if external
                 else "within the caller's reach")
        raise PolicyError(
            f"verify={requested!r} ({set_by}) is below the operator minimum "
            f"verify={minimum!r}, set by {source} ({reach}). A caller cannot lower "
            "it. Ask for verify='gate' or 'round', or have the operator change the policy.")
    return VerifyDecision(requested, set_by, minimum, source, external,
                          require_independent()[0])


def decide_manifest(stages) -> VerifyDecision:
    """Apply the operator minimum to a custom manifest (``rapier run --manifest``).

    What the minimum holds is whatever the caller gets back. A manifest that
    writes an answer counts as ``verify=off`` unless a citation gate runs after
    the last stage that writes the answer and before the output is fixed (at
    compose, or at the end when there is none). Only a manifest that writes no
    answer at all (Frame, Proposer-only) has nothing for the floor to hold."""
    from .pipeline import manifest_declares, writes_answer

    names = [s.stage for s in stages]
    if not any(writes_answer(n) for n in names):
        mode = "gate"
    elif not manifest_declares(stages, "citation_gate"):
        mode = "off"
    else:
        mode = "round" if names.count("citation_gate") > 1 else "gate"
    return decide_verify(mode, "custom manifest")


def require_independent() -> tuple[bool, str, bool]:
    """``(required, source, external)``: does the operator require an independent,
    second-vendor review? Set only in the policy file (D-68: operator-settable
    only; there is no tool argument or environment variable for it)."""
    got = _read_policy_file()
    if got is None or not got[0].get("require_independent"):
        return False, "", False
    _data, path, external = got
    return True, path, external


def decide_independence(policy=None, available: list[str] | None = None) -> None:
    """Refuse the run when the operator requires an independent review and no
    second vendor is available to give one. Raises :class:`PolicyError`."""
    from .models import Policy, available_vendors

    required, source, external = require_independent()
    if not required:
        return
    avail = list(available if available is not None else available_vendors())
    try:
        _primary, second = (policy or Policy()).resolve(avail)
    except Exception:  # noqa: BLE001 — e.g. no vendors at all
        second = None
    if second is None:
        reach = "out of the caller's reach" if external else "within the caller's reach"
        raise PolicyError(
            "the operator requires an independent review from a second vendor "
            f"({source}, {reach}), but only one vendor is available "
            f"({', '.join(v for v in avail if v != 'mock') or 'none'}). Add a second "
            "vendor's key, or have the operator change the policy. Nothing was run.")
