"""Tool call validity check and the Phase 0 model selection rule.

A valid tool call: the tool name is in the list, the arguments can be read as a JSON object, the required fields are
present, there is no field outside the list, the types and the option (enum) values are right. Types are checked
strictly by JSON Schema: where an integer is expected, the text "60" is invalid.

Selection rule (Phase 0 acceptance criterion): after the cache is warm, the largest model that finishes every
tool-calling turn in at most 30 seconds and produces a valid tool call in 5 of the 5 sample questions becomes the demo
model. If no candidate satisfies it, the rule is not relaxed; the result is reported as "seçilemedi" (could not be
selected).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

from sayman.model_selection.tool_drafts import TOOLS_BY_NAME

DURATION_THRESHOLD_S = 30.0
REQUIRED_VALID = 5

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_arguments(arguments: Any) -> tuple[dict | None, str | None]:
    """Converts the arguments to a dictionary. If there is an error, returns (None, reason)."""
    if isinstance(arguments, dict):
        return arguments, None
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return None, "argümanlar JSON olarak okunamadı"
        if not isinstance(parsed, dict):
            return None, "argümanlar bir JSON nesnesi değil"
        return parsed, None
    if arguments is None:
        return {}, None
    return None, f"argüman tipi beklenmiyor: {type(arguments).__name__}"


def _check_value(field: str, value: Any, schema: dict) -> str | None:
    type_name = schema.get("type")
    if type_name == "string":
        if not isinstance(value, str):
            return f"'{field}' metin olmalı"
        if "enum" in schema and value not in schema["enum"]:
            return f"'{field}' için izin verilmeyen değer: {value!r}"
        if schema.get("format") == "date":
            if not _DATE_PATTERN.match(value):
                return f"'{field}' YYYY-MM-DD biçiminde değil"
            try:
                _dt.date.fromisoformat(value)
            except ValueError:
                return f"'{field}' geçerli bir tarih değil"
        return None
    if type_name == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{field}' tamsayı olmalı"
        return None
    if type_name == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{field}' sayı olmalı"
        return None
    if type_name == "boolean":
        if not isinstance(value, bool):
            return f"'{field}' doğru/yanlış olmalı"
        return None
    return f"'{field}' için bilinmeyen şema tipi: {type_name}"


def is_tool_call_valid(call: dict, tools: dict | None = None) -> tuple[bool, str]:
    """Checks a single tool call. call = {"ad": ..., "argumanlar": ...}."""
    tools = TOOLS_BY_NAME if tools is None else tools
    name = call.get("ad")
    if name not in tools:
        return False, f"bilinmeyen araç: {name!r}"
    arguments, error = parse_arguments(call.get("argumanlar"))
    if error:
        return False, error
    schema = tools[name]["function"]["parameters"]
    properties = schema.get("properties", {})
    missing = [z for z in schema.get("required", []) if z not in arguments]
    if missing:
        return False, "eksik zorunlu alan: " + ", ".join(missing)
    extra = [k for k in arguments if k not in properties]
    if extra and schema.get("additionalProperties") is False:
        return False, "tanımsız alan: " + ", ".join(extra)
    for field, value in arguments.items():
        error = _check_value(field, value, properties[field])
        if error:
            return False, error
    return True, "geçerli"


def is_response_valid(calls: list[dict], tools: dict | None = None) -> tuple[bool, str]:
    """Checks all the tool calls in a model response; at least one call is required."""
    if not calls:
        return False, "araç çağrısı yok"
    for call in calls:
        valid, reason = is_tool_call_valid(call, tools)
        if not valid:
            return False, reason
    return True, "geçerli"


def is_expected_call(calls: list[dict], expected_tool: str, expected_arguments: dict) -> bool:
    """Is one of the calls the expected tool, and does it carry the expected arguments exactly?"""
    for call in calls:
        if call.get("ad") != expected_tool:
            continue
        arguments, error = parse_arguments(call.get("argumanlar"))
        if error:
            continue
        if all(arguments.get(k) == v for k, v in expected_arguments.items()):
            return True
    return False


def meets_rule(candidate: dict, threshold_s: float = DURATION_THRESHOLD_S, required: int = REQUIRED_VALID) -> bool:
    """candidate = {"model", "parametre_milyar", "turlar": [{"sure_s", "gecerli"}, ...]}."""
    turns = candidate.get("turlar", [])
    if len(turns) < required:
        return False
    if any(t["sure_s"] > threshold_s for t in turns):
        return False
    return sum(1 for t in turns if t["gecerli"]) >= required


def selection_rule(candidates: list[dict], threshold_s: float = DURATION_THRESHOLD_S, required: int = REQUIRED_VALID) -> str | None:
    """The name of the largest model that satisfies the rule, None if none of them does."""
    passing = [a for a in candidates if meets_rule(a, threshold_s, required)]
    if not passing:
        return None
    return max(passing, key=lambda a: a["parametre_milyar"])["model"]
