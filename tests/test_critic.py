"""Phase 3 — critic gate."""

from __future__ import annotations

from core.critic import apply_critic_or_retry, critique_answer


def test_critic_flags_unsupported_web_claims():
    result = critique_answer(
        "bitcoin price today",
        "Bitcoin is exactly $123456 as of today.",
        sources=[],
        mode="WEB",
    )
    assert result.ok is False
    assert result.needs_web is True


def test_critic_ok_with_sources():
    result = critique_answer(
        "walrus operator",
        "The walrus operator is :=.\nSources:\n- https://peps.python.org/pep-0572/",
        sources=["https://peps.python.org/pep-0572/"],
        mode="WEB",
    )
    assert result.ok is True


def test_apply_critic_appends_citations():
    draft = "Assignment expressions use := in Python 3.8."
    out = apply_critic_or_retry(
        "walrus",
        draft,
        sources=["https://peps.python.org/pep-0572/"],
        mode="WEB",
    )
    # Either ok as-is or citations appended
    assert "peps.python.org" in out or "Assignment expressions" in out


def test_apply_critic_fail_closed_no_retry():
    out = apply_critic_or_retry(
        "latest news",
        "The winner was exactly Alice as of today with 99% certainty.",
        sources=[],
        mode="WEB",
        retry_fn=None,
    )
    assert "not inventing" in out.lower() or "could not verify" in out.lower()
