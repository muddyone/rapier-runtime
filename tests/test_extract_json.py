"""extract_json must salvage a truncated JSON array rather than fail the whole
call — the gate's specifics enumeration can be cut off mid-element by an
output-token cap (the bug that sent /sparring's definitiveness gate to
'unchecked')."""
from __future__ import annotations

from rapier.verify._vendor.lib_llm import extract_json


def test_normal_json_still_parses():
    assert extract_json('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_salvages_truncated_wrapped_array():
    # {"specifics": [ ... ]} cut off mid-third-element
    txt = '{"specifics": [{"text":"75 tests","value":75},{"text":"9 projects","value":9},{"text":"trun'
    out = extract_json(txt)
    assert out == {"specifics": [{"text": "75 tests", "value": 75},
                                 {"text": "9 projects", "value": 9}]}


def test_brace_inside_string_does_not_fool_salvage():
    # a '}' inside a string value must not be mistaken for an element boundary
    txt = '{"specifics": [{"text":"has a } brace","value":1},{"text":"tru'
    assert extract_json(txt) == {"specifics": [{"text": "has a } brace", "value": 1}]}


def test_unsalvageable_still_raises():
    from rapier.verify._vendor.lib_llm import LLMError

    try:
        extract_json("not json at all, no brackets")
        assert False, "expected LLMError"
    except LLMError:
        pass
