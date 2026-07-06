"""V4: vendor-pair policy + the lib_llm shim (backend swap, honest degradation)."""
from __future__ import annotations

from rapier.models import resolve_pair


# --- policy: resolve_pair ----------------------------------------------------
def test_resolve_pair_picks_two_distinct_frontier_vendors():
    assert resolve_pair(["mock", "gemini", "xai"]) == ("gemini", "xai")


def test_resolve_pair_single_vendor_degrades_secondary_to_none():
    assert resolve_pair(["mock", "xai"]) == ("xai", None)


def test_resolve_pair_no_vendors():
    assert resolve_pair(["mock"]) == (None, None)


def test_resolve_pair_honors_primary_pref_and_picks_distinct_secondary():
    primary, secondary = resolve_pair(["mock", "openai", "gemini"], primary_pref="gemini")
    assert primary == "gemini"
    assert secondary in ("openai",) and secondary != primary


def test_resolve_pair_secondary_pref_when_available():
    primary, secondary = resolve_pair(
        ["mock", "anthropic", "gemini", "xai"], primary_pref="anthropic", secondary_pref="xai"
    )
    assert (primary, secondary) == ("anthropic", "xai")


# --- shim: slot binding + honest keys_present --------------------------------
def test_shim_keys_present_reflects_binding(monkeypatch):
    from rapier.verify import _bootstrap as B

    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("XAI_API_KEY", "xai-xxxxxxxxxxxxxxxx")
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.xxxxxxxxxxxxxxxx")

    # bind primary=gemini, secondary=xai -> both slots available, no anthropic
    B.bind_slots(("gemini", "gemini-2.5-flash"), ("xai", "grok-4.3"))
    try:
        kp = B.keys_present()
        assert kp == {"anthropic": True, "openai": True}  # slots, not literal vendors
    finally:
        B.reset_slots()


def test_shim_single_vendor_binding_reports_secondary_absent(monkeypatch):
    from rapier.verify import _bootstrap as B

    for env in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.xxxxxxxxxxxxxxxx")

    B.bind_slots(("gemini", "gemini-2.5-flash"), None)  # single vendor
    try:
        kp = B.keys_present()
        assert kp["anthropic"] is True and kp["openai"] is False
    finally:
        B.reset_slots()


def test_shim_routes_non_default_vendor_through_rapier(monkeypatch):
    """A slot bound to gemini/xai must use the Rapier model layer, not the
    original anthropic/openai clients."""
    from rapier.verify import _llm_shim
    import rapier.models as M

    calls = {}

    class _FakeClient:
        def __init__(self, spec):
            self.spec = spec

        def complete(self, system, prompt):
            calls["vendor"] = self.spec.vendor
            from rapier.models import ModelResponse

            return ModelResponse(text='{"ok": true}', vendor=self.spec.vendor, model=self.spec.model)

    monkeypatch.setattr(M, "build_client", lambda spec: _FakeClient(spec))
    _llm_shim.bind_slots(("gemini", "gemini-2.5-flash"), ("xai", "grok-4.3"))
    try:
        out = _llm_shim.claude_json("ignored", "sys", "user")
        assert out == {"ok": True}
        assert calls["vendor"] == "gemini"  # primary slot routed to gemini
        _llm_shim.gpt_json("ignored", "sys", "user")
        assert calls["vendor"] == "xai"  # secondary slot routed to xai
    finally:
        _llm_shim.reset_slots()


def test_shim_unbound_delegates_to_original(monkeypatch):
    """Unbound, keys_present must mirror the real ANTHROPIC/OPENAI env (parity)."""
    from rapier.verify import _bootstrap as B

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxxxxxxxxxxxxxxx")
    B.reset_slots()
    kp = B.keys_present()
    assert kp == {"anthropic": False, "openai": True}
