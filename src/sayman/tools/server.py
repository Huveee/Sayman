"""Sayman MCP server: exposes the tools to the model; every call goes through a single gate.

Gate order: identity card -> does the tool exist -> may the role call this tool -> do the arguments match the
schema -> is the date later than "today" -> run the tool on the read-only connection in the role's scope.
Whatever the outcome (success, refusal, error), the call is written to the audit log; the identity card is not.

The MCP library's own input validation is turned off (validate_input=False): if it were on, a request with bad
arguments would return without going through the identity check and the audit log. The gate does the check.

Running (stdio, from the project root):
    .venv\\Scripts\\python -I scripts\\phase03_server.py
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server

from sayman.tools import scope as scope_module
from sayman.tools.catalog import TOOLS, TOOLS_BY_NAME, ToolError, ToolUnavailable, Context
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import IdentityError, verify_card, card_from_meta
from sayman.tools.permissions import PermissionTable
from sayman.data.clock import Clock

SCOPE_LABELS = {"hepsi": "tüm şirket", "yurtici": "yalnızca yurtiçi müşteriler", "ihracat": "yalnızca yurt dışı müşteriler"}
MESSAGES = {
    "kimlik_gecersiz": "Kimlik doğrulanamadı.",
    "bilinmeyen_arac": "Böyle bir araç yok.",
    "yetki_yok": "Bu bilgiyi görme yetkiniz yok.",
    "hata": "Araç çalışırken bir hata oluştu.",
    "kullanilamaz": "Bu araç şu an kullanılamıyor.",
}
MAX_ARGUMENT_LENGTH = 2000  # upper limit on the argument text written to the audit log


class Refusal(Exception):
    def __init__(self, code: str, message: str | None = None, reason: str | None = None):
        super().__init__(code)
        self.code, self.message, self.reason = code, message or MESSAGES[code], reason or code


def _loggable(arguments) -> object:
    text = json.dumps(arguments, ensure_ascii=False, default=str)
    return arguments if len(text) <= MAX_ARGUMENT_LENGTH else text[:MAX_ARGUMENT_LENGTH] + "…"


TYPE_NAMES = {"string": "metin", "integer": "tam sayı"}


def argument_message(error: jsonschema.ValidationError, schema: dict) -> str:
    """Describes a schema error using only names from the schema; does not echo the user's input back (keeps the answer short)."""
    properties = schema["properties"]
    field_name = error.path[0] if error.path and error.path[0] in properties else None
    v = error.validator
    if v == "additionalProperties":
        return "Bilinmeyen argüman. Geçerli argümanlar: " + ", ".join(properties) + "."
    if v == "required":
        return "Eksik argüman: " + ", ".join(a for a in schema["required"] if a not in error.instance) + "."
    if field_name is None:
        return "Argümanlar geçersiz."
    o = properties[field_name]
    if v == "format":
        return f"{field_name} YYYY-MM-DD biçiminde geçerli bir tarih olmalı."
    if v == "enum":
        return f"{field_name} şunlardan biri olmalı: {', '.join(o['enum'])}."
    if v == "maximum":  # Phase 9 third arm: a model that exceeds the limit should know what to do
        return f"{field_name} en çok {o['maximum']} olabilir; {o['maximum']} ile yeniden deneyin."
    if v == "minimum":
        return f"{field_name} {o['minimum']} ile {o['maximum']} arasında olmalı."
    if v == "maxLength":
        return f"{field_name} en çok {o['maxLength']} karakter olabilir."
    if v == "minLength":
        return f"{field_name} en az {o['minLength']} karakter olmalı."
    if v == "type":
        return f"{field_name} {TYPE_NAMES.get(o.get('type'), 'geçerli bir değer')} olmalı."
    return f"{field_name} geçersiz."


def _is_future(value, today: dt.date) -> bool:
    return isinstance(value, str) and dt.date.fromisoformat(value) > today


class SaymanServer:
    def __init__(self, warehouse: Path, clock: Callable[[], Clock], key: bytes, permissions: PermissionTable,
                 audit_log: AuditLog, scope_dir: Path | None = None, time_fn: Callable[[], float] = time.time,
                 belge=None, search_method: str = "anlam"):
        """belge: document searcher (if not given, belge_ara is refused with "kullanilamaz"). search_method:
        "anlam" (default), "bm25", "karma" or "karma_sirala" (reranker; for a more powerful computer)."""
        if not key:
            raise RuntimeError("imza anahtarı olmadan sunucu açılmaz")
        self.clock, self.key, self.permissions, self.audit_log, self.time_fn = clock, key, permissions, audit_log, time_fn
        self.belge, self.search_method = belge, search_method
        directory = Path(scope_dir) if scope_dir else Path(tempfile.mkdtemp(prefix="sayman_kapsam_"))
        self.connections = scope_module.connections(warehouse, directory, {r.scope for r in permissions.roller.values()})
        self.validators = {a.name: jsonschema.Draft202012Validator(a.schema, format_checker=jsonschema.FormatChecker())
                               for a in TOOLS}
        self.mcp = Server("sayman")
        self.mcp.list_tools()(self._list_tools)
        self.mcp.call_tool(validate_input=False)(self._call_tool)

    # -- MCP endpoints ----------------------------------------------------------------------------

    @staticmethod
    def tools() -> list[types.Tool]:
        return [types.Tool(name=a.name, description=a.description, inputSchema=a.schema,
                           annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                             idempotentHint=True, openWorldHint=False))
                for a in TOOLS]

    async def _list_tools(self) -> list[types.Tool]:
        return self.tools()

    async def _call_tool(self, name: str, arguments: dict) -> types.CallToolResult:
        try:
            meta = self.mcp.request_context.meta
        except LookupError:
            meta = None
        response, error = self.gate(name, arguments, card_from_meta(meta))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False))],
                                    structuredContent=response, isError=error)

    # -- gate ---------------------------------------------------------------------------------------

    def gate(self, name, arguments, card) -> tuple[dict, bool]:
        """The single gate. Returns (response, is_error); in every case writes one row to the audit log."""
        call_id = "C" + secrets.token_hex(6)
        started = time.perf_counter()
        row = {"cagri_id": call_id, "arac": (name if isinstance(name, str) else repr(name))[:100],
                 "argumanlar": _loggable(arguments), "kullanici": None, "rol": None}
        try:
            try:
                identity = verify_card(card, self.key, self.permissions.users, self.time_fn())
            except IdentityError as e:
                raise Refusal("kimlik_gecersiz", reason=e.reason) from None
            row.update(kullanici=identity.user, rol=identity.role)
            tool = TOOLS_BY_NAME.get(name)
            if tool is None:
                raise Refusal("bilinmeyen_arac")
            if not self.permissions.is_allowed(identity.role, name):
                raise Refusal("yetki_yok")
            if not isinstance(arguments, dict):
                raise Refusal("gecersiz_arguman", "Argümanlar bir nesne olmalı.")
            error = next(iter(sorted(self.validators[name].iter_errors(arguments), key=lambda e: list(e.path))), None)
            if error is not None:
                raise Refusal("gecersiz_arguman", "Argüman hatası: " + argument_message(error, tool.schema))
            # JSON Schema also counts 5.0 as an integer; the tools should receive a real int.
            arguments = {k: int(v) if isinstance(v, float) and tool.properties[k].get("type") == "integer" else v
                          for k, v in arguments.items()}
            today = self.clock().simdi
            row["simdi"] = today.isoformat()  # simulation day: the call can be reproduced exactly from this row
            for field_name in tool.date_fields:
                if _is_future(arguments.get(field_name), today):
                    raise Refusal("gelecek_tarih", f"Bugünden ({today.isoformat()}) sonraki günler için veri yok.")
            scope = self.permissions.roller[identity.role].scope
            context = Context(self.connections[scope], today, identity.role, self.belge, self.search_method)
            try:
                result_date, result, sources = tool.run(context, arguments)
            except ToolError as e:
                raise Refusal("gecersiz_arguman", str(e)) from None
            except ToolUnavailable as e:
                raise Refusal("kullanilamaz", str(e)) from None
            response = {"cagri_id": call_id, "arac": name, "tarih": result_date.isoformat(), "kapsam": SCOPE_LABELS[scope],
                     "sonuc": result, "kaynak": sources}
            row.update(sonuc="tamam", tarih=result_date.isoformat(), kayit_sayisi=sum(len(v) for v in sources.values()))
            return response, False
        except Refusal as r:
            row.update(sonuc="reddedildi", neden=r.reason)
            return {"cagri_id": call_id, "hata": r.code, "mesaj": r.message}, True
        except Exception as e:  # unexpected error: the details go only to the log
            row.update(sonuc="hata", neden=f"{type(e).__name__}: {str(e)[:300]}")
            return {"cagri_id": call_id, "hata": "hata", "mesaj": MESSAGES["hata"]}, True
        finally:
            row["sure_ms"] = round((time.perf_counter() - started) * 1000, 1)
            self.audit_log.write(**row)

    def close(self) -> None:
        for con in self.connections.values():
            con.close()


def main(argv: list[str] | None = None) -> None:
    """stdio server. The key is read first from the environment variable, otherwise from the .env file in the project root."""
    import argparse
    import os

    import anyio
    from dotenv import dotenv_values
    from mcp.server.stdio import stdio_server

    from sayman.tools.identity import KEY_ENV_VAR, read_key
    from sayman.tools.permissions import read_permissions
    from sayman.documents.search import METHODS, build_searcher
    from sayman.data.clock import read_clock

    root = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(description="Sayman MCP sunucusu (stdio)")
    ap.add_argument("--ambar", type=Path, default=root / "data" / "ambar" / "sayman.duckdb")
    ap.add_argument("--saat", type=Path, default=root / "data" / "saat.json")
    ap.add_argument("--kayit", type=Path, default=root / "data" / "denetim" / "kayit.jsonl")
    ap.add_argument("--kapsam-klasoru", type=Path, default=root / "data" / "ambar" / "kapsam")
    ap.add_argument("--belge", type=Path, default=None,
                    help="Faz 5 belge kütüphanesi klasörü (ör. data/belge); verilmezse belge_ara kapalıdır")
    ap.add_argument("--belge-yontemi", choices=METHODS, default="anlam",
                    help="karma_sirala sıralayıcı modeli yükler; daha güçlü bir bilgisayar için")
    ap.add_argument("--siralayici-modeli", type=Path, default=root / "data" / "modeller" / "bge-reranker-v2-m3")
    a = ap.parse_args(argv)
    belge = build_searcher(a.belge, a.belge_yontemi, a.siralayici_modeli) if a.belge else None
    env = dict(dotenv_values(root / ".env"))
    if os.environ.get(KEY_ENV_VAR):
        env[KEY_ENV_VAR] = os.environ[KEY_ENV_VAR]
    server = SaymanServer(warehouse=a.ambar, clock=lambda: read_clock(a.saat), key=read_key(env),
                            permissions=read_permissions(), audit_log=AuditLog(a.kayit), scope_dir=a.kapsam_klasoru,
                            belge=belge, search_method=a.belge_yontemi)

    async def serve():
        async with stdio_server() as (read_stream, write_stream):
            await server.mcp.run(read_stream, write_stream, server.mcp.create_initialization_options())

    try:
        anyio.run(serve)
    finally:
        server.close()
