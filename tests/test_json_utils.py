from core.json_utils import parse_llm_json, strip_llm_fences


def test_parse_plain_json():
    assert parse_llm_json('{"a": 1}')["a"] == 1


def test_parse_fenced_json():
    raw = 'Here:\n```json\n{"b": 2}\n```\n'
    assert parse_llm_json(raw)["b"] == 2


def test_strip_think_blocks():
    raw = '<think>secret</think>{"c": 3}'
    assert parse_llm_json(raw)["c"] == 3


def test_strip_generic_fence():
    assert "hello" in strip_llm_fences("```\nhello\n```")
