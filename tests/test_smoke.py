"""Smoke tests: is the environment set up correctly, are the basic files in place?

These tests do not go to the internet or the Ollama server.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sayman.model_selection.tool_drafts import TOOLS, TOOLS_BY_NAME
from sayman.model_selection.call_validation import is_tool_call_valid
from sayman.model_selection.sample_questions import SAMPLE_QUESTIONS

ROOT = Path(__file__).resolve().parents[1]


def test_python_version_3_11():
    assert sys.version_info[:2] == (3, 11)


def test_packages_import():
    import dotenv  # noqa: F401
    import ollama  # noqa: F401
    import sayman

    assert sayman.__version__


def test_gitignore_keeps_secret_files_out():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert ".venv/" in lines


def test_env_file_is_not_tracked_in_git():
    result = subprocess.run(
        ["git", "ls-files", "--", ".env"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


def test_fifteen_tool_definitions_exist_and_names_are_unique():
    names = [a["function"]["name"] for a in TOOLS]
    assert len(names) == 15
    assert len(set(names)) == 15


def test_tool_definitions_are_well_formed():
    for tool in TOOLS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["description"].strip()
        schema = function["parameters"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"]), function["name"]
        for field, alt in schema["properties"].items():
            assert alt["type"] in {"string", "integer", "number", "boolean"}, (function["name"], field)
            assert alt.get("description", "").strip(), (function["name"], field)
            if "enum" in alt:
                assert alt["enum"], (function["name"], field)


def test_five_sample_questions_and_expected_tools_exist():
    assert len(SAMPLE_QUESTIONS) == 5
    for s in SAMPLE_QUESTIONS:
        assert s["beklenen_arac"] in TOOLS_BY_NAME, s["kimlik"]
    with_arguments = [s for s in SAMPLE_QUESTIONS if s["beklenen_argumanlar"]]
    assert len(with_arguments) >= 2


def test_expected_arguments_fit_schema():
    """The expected arguments must be valid (once the missing required ones are filled in with an example value)."""
    example_value = {"string": "ornek", "integer": 1, "number": 1.0, "boolean": True}
    for s in SAMPLE_QUESTIONS:
        schema = TOOLS_BY_NAME[s["beklenen_arac"]]["function"]["parameters"]
        arguments = dict(s["beklenen_argumanlar"])
        for required_name in schema["required"]:
            if required_name not in arguments:
                alt = schema["properties"][required_name]
                arguments[required_name] = alt["enum"][0] if "enum" in alt else example_value[alt["type"]]
        valid, reason = is_tool_call_valid({"ad": s["beklenen_arac"], "argumanlar": arguments})
        assert valid, (s["kimlik"], reason)
