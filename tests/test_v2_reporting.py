"""Regression checks for job costs and token claims after interrupted attempts."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from report_release_v2 import aggregate, pareto  # noqa: E402


def episode(*, prior=None, tokens=None):
    result = {
        "status": "complete",
        "replay_verified": True,
        "model": {"key": "fixture", "provider": "Test"},
        "spec": {"tier": "generated-3", "seed": 7, "orders": [{}, {}, {}]},
        "dollar_cap": 10,
        "token_cap": tokens,
        "quality": 100,
        "cost_usd": 5,
        "accepted_cost_usd": 5,
        "boundary_cost_usd": 0,
        "completed": 3,
        "invalid": 0,
        "output_tokens": 12,
        "reason": "game_terminal",
        "requests": [{"response": {"choices": [{"finish_reason": "stop"}]}}],
    }
    if prior:
        result["_prior_attempt"] = prior
    return result


def test_failed_prefix_cost_summed_not_averaged_and_unknown_not_actual():
    ep = episode(
        prior={"known_cost_usd": 2, "retained_liability_usd": 3, "failure_kind": "provider_failure"}
    )
    before = copy.deepcopy(ep)
    row = aggregate([ep])["rows"][0]
    assert row["mean_completed_attempt_cost_usd"] == 5
    assert row["mean_cost_usd"] == 7
    assert row["mean_retained_liability_usd"] == 3
    assert row["mean_accounted_cost_usd"] == 10
    assert row["attempts"] == 2 and row["scheduled_jobs"] == 1
    assert row["provider_failures"] == 1 and row["peer_accounting_pauses"] == 0
    assert row["first_attempt_completed_jobs"] == 0
    assert pareto([row]) == []
    assert ep == before


def test_peer_pause_cost_retained_without_provider_failure_or_extra_seed():
    resumed = episode(
        prior={
            "known_cost_usd": 2,
            "retained_liability_usd": 0,
            "failure_kind": "peer_accounting_pause",
        }
    )
    first_try = episode()
    row = aggregate([resumed, first_try])["rows"][0]
    assert row["mean_cost_usd"] == pytest.approx((7 + 5) / 2)
    assert row["peer_accounting_pauses"] == 1 and row["provider_failures"] == 0
    assert row["attempts"] == 3 and row["scheduled_jobs"] == 2
    assert row["seeds"] == 1 and row["ci95"] is None
    assert not row["unresolved_cost"]


def test_replaced_token_cell_cannot_claim_b80_even_if_completed_score_is_100():
    ep = episode(
        tokens=512,
        prior={
            "known_cost_usd": 0.1,
            "retained_liability_usd": 0,
            "failure_kind": "peer_accounting_pause",
        },
    )
    result = aggregate([ep])
    assert result["rows"][0]["quality"] == 100
    assert result["rows"][0]["replacement_affected"] == 1
    assert result["B80"]["fixture"]["mean"] is None
    untouched = aggregate([episode(tokens=512)])
    assert untouched["B80"]["fixture"]["mean"] == 512
