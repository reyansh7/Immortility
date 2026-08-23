"""FAST / AGENT / BACKGROUND strategy classification."""

from core.execution_mode import MODE_AGENT, MODE_BACKGROUND, MODE_FAST, classify_strategy
from core.router import classify_strategy as router_classify


def test_simple_math_is_fast():
    assert classify_strategy("what is 2+2") == MODE_FAST
    assert router_classify("what is 2+2") == MODE_FAST


def test_change_query_is_agent_strategy():
    assert classify_strategy("i made changes in your code base find out those changes") == MODE_AGENT
    assert classify_strategy("what changed in this repo") == MODE_AGENT


def test_long_ingest_is_background():
    assert classify_strategy("index my desktop projects") == MODE_BACKGROUND
    assert classify_strategy("full audit of the whole tree") == MODE_BACKGROUND
    assert classify_strategy("analyze this 5000 page document") == MODE_BACKGROUND


def test_unseen_phrasing_is_not_a_catalog_lookup():
    """Must classify by strategy, not a 25-task name table."""
    msg = (
        "Could you tally how many open pull requests mention flaky tests "
        "in this repository?"
    )
    assert classify_strategy(msg) == MODE_AGENT


def test_greeting_is_fast():
    assert classify_strategy("hello") == MODE_FAST
    assert classify_strategy("what model are you") == MODE_FAST
    assert classify_strategy("tell me what you can do now") == MODE_FAST
