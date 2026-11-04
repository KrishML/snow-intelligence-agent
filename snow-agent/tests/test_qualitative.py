"""
Property-based and concrete tests for _compute_score — task 5.2.

Uses Hypothesis to verify:
  Property 1a: result is always in [0, 20]
  Property 1b: positive >= neutral >= negative for the same keyword count
  Property 1c: positive sentiment always >= 15 (floor guarantee)
  Property 1d: neutral sentiment always returns exactly 10

Also includes concrete spec test cases from the design document.

**Validates: Requirements 10.4**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from app.scoring.qualitative import _compute_score

SENTIMENTS = ["positive", "neutral", "negative"]


# ---------------------------------------------------------------------------
# Property 1a: score is always in [0, 20]
# ---------------------------------------------------------------------------

@given(
    sentiment=st.sampled_from(SENTIMENTS),
    n_keywords=st.integers(min_value=0, max_value=10),
)
@h_settings(max_examples=200, deadline=None)
def test_qualitative_score_bounds(sentiment, n_keywords):
    """Property 1a: result is always in [0, 20]."""
    kw = ["icy"] * n_keywords
    result = _compute_score(sentiment, kw)
    assert 0 <= result <= 20


# ---------------------------------------------------------------------------
# Property 1b: positive >= neutral >= negative (monotonicity)
# ---------------------------------------------------------------------------

@given(n_keywords=st.integers(min_value=0, max_value=10))
@h_settings(max_examples=200, deadline=None)
def test_qualitative_score_monotonicity(n_keywords):
    """Property 1b: positive >= neutral >= negative for same keyword count."""
    kw = ["icy"] * n_keywords
    pos = _compute_score("positive", kw)
    neu = _compute_score("neutral", kw)
    neg = _compute_score("negative", kw)
    assert pos >= neu >= neg


# ---------------------------------------------------------------------------
# Property 1c: positive sentiment floor at 15
# ---------------------------------------------------------------------------

@given(n_keywords=st.integers(min_value=0, max_value=10))
@h_settings(max_examples=200, deadline=None)
def test_positive_score_floor(n_keywords):
    """Positive sentiment score is always >= 15 regardless of keywords."""
    kw = ["icy"] * n_keywords
    assert _compute_score("positive", kw) >= 15


# ---------------------------------------------------------------------------
# Property 1d: neutral sentiment is fixed at 10
# ---------------------------------------------------------------------------

@given(n_keywords=st.integers(min_value=0, max_value=10))
@h_settings(max_examples=200, deadline=None)
def test_neutral_score_fixed(n_keywords):
    """Neutral sentiment always returns exactly 10."""
    kw = ["icy"] * n_keywords
    assert _compute_score("neutral", kw) == 10


# ---------------------------------------------------------------------------
# Concrete spec test cases (from design document)
# ---------------------------------------------------------------------------

def test_positive_no_keywords():
    """positive, no keywords → 20 (base with no penalty)."""
    assert _compute_score("positive", []) == 20


def test_neutral_no_keywords():
    """neutral, no keywords → 10 (fixed)."""
    assert _compute_score("neutral", []) == 10


def test_negative_no_keywords():
    """negative, no keywords → 5 (base with no penalty)."""
    assert _compute_score("negative", []) == 5


def test_positive_three_keywords_floor():
    """positive, 3 keywords (penalty=min(6,5)=5, score=max(15,15)=15)."""
    assert _compute_score("positive", ["icy", "slush", "closed"]) == 15


def test_negative_three_keywords_floor():
    """negative, 3 keywords (penalty=min(6,5)=5, score=max(0,0)=0)."""
    assert _compute_score("negative", ["icy", "slush", "closed"]) == 0
