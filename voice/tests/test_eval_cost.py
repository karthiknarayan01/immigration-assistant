"""Cost accounting for the eval report.

The report's headline is score per dollar, so a mispriced run is a wrong
conclusion. A turn can mix models — judgement questions go to the reasoner —
which a single price pair would misprice, and an unpriced run must not read as
a free one.
"""

from app.config import settings
from evals.run_eval import cost_usd, token_totals


def _with_prices(value: str) -> str:
    original = settings.llm_prices
    settings.llm_prices = value
    return original


def test_cost_is_priced_per_model():
    original = _with_prices('{"cheap/model": [1.0, 2.0], "reasoning/model": [10.0, 20.0]}')
    try:
        usage = {
            "cheap/model": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            "reasoning/model": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
        }
        # 1 (prompt) + 2 (completion) + 10 + 20
        assert round(cost_usd(usage), 6) == 33.0
    finally:
        settings.llm_prices = original


def test_unpriced_models_contribute_nothing():
    original = _with_prices('{"cheap/model": [1.0, 1.0]}')
    try:
        usage = {
            "cheap/model": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
            "mystery/model": {"prompt_tokens": 5_000_000, "completion_tokens": 5_000_000},
        }
        assert round(cost_usd(usage), 6) == 1.0
    finally:
        settings.llm_prices = original


def test_no_prices_means_no_cost():
    original = _with_prices("")
    try:
        assert cost_usd({"a/model": {"prompt_tokens": 1000, "completion_tokens": 1000}}) == 0.0
    finally:
        settings.llm_prices = original


def test_malformed_prices_do_not_crash():
    original = _with_prices("not json at all")
    try:
        assert cost_usd({"a/model": {"prompt_tokens": 10, "completion_tokens": 10}}) == 0.0
    finally:
        settings.llm_prices = original


def test_token_totals_sum_across_models():
    usage = {
        "cheap/model": {"prompt_tokens": 100, "completion_tokens": 10},
        "reasoning/model": {"prompt_tokens": 200, "completion_tokens": 20},
    }
    assert token_totals(usage) == (300, 30)
    assert token_totals({}) == (0, 0)
