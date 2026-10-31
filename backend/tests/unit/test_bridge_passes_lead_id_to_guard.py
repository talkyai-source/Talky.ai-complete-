"""The bridge origination path must give CallGuard the lead id.

`leads.do_not_call` is enforced by CallGuard only when it is handed a
``lead_id`` — a guard wired to a signal that is never supplied is this
repo's recurring trap. The dialer supplies it; this pins that the HTTP
origination endpoint, which already accepts ``lead_id`` in its payload,
supplies it too. Without this a caller reaching the bridge directly gets
only the tenant DNC-list check and a lead the customer flagged
do-not-call is dialled.

Scope, deliberately: this is a WIRING guard, not behavioural proof. It
parses the real ``guard.evaluate(...)`` call node rather than grepping
text, so it proves the argument is passed at the call site — nothing
more. That CallGuard actually blocks a flagged lead is covered
behaviourally in ``test_lead_do_not_call_suppression.py``. The two
together are the proof; neither is sufficient alone. A behavioural test
here would need the whole container/adapter/caller-id scaffold that
``test_telephony_bridge_auth.py`` documents itself as stopping short of.
"""
import ast
import pathlib


def _guard_evaluate_kwargs() -> set[str]:
    src = pathlib.Path("app/api/v1/endpoints/telephony_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "evaluate"
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "guard"
        ):
            return {kw.arg for kw in node.keywords if kw.arg}
    raise AssertionError("no guard.evaluate(...) call found in telephony_bridge.py")


def test_bridge_hands_the_lead_id_to_the_call_guard():
    kwargs = _guard_evaluate_kwargs()
    assert "lead_id" in kwargs, (
        "telephony_bridge origination calls guard.evaluate() without lead_id, so "
        "CallGuard cannot apply the per-lead do_not_call check on this path"
    )


def test_bridge_still_passes_the_core_guard_inputs():
    kwargs = _guard_evaluate_kwargs()
    assert {"tenant_id", "phone_number", "campaign_id", "call_type"} <= kwargs
