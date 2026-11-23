"""Phase 9: tools generated automatically from the OpenAPI definition of the fake API, and their gate.

The tools are not written by hand: fastmcp.FastMCP.from_openapi generates one tool for every endpoint; its name,
description and argument schema come from the OpenAPI definition. These definitions are shown to the model as they are
(tool_definitions). The role header (X-Rol) is a security scheme and does not enter the arguments; the client of the
gate puts the header, the model cannot change it.

The gate (GeneratedServer.gate) follows the same order as the gate of the hand-written tools (`sayman.tools.server`)
and returns the same envelope: identity card -> does the tool exist -> do the arguments fit the schema -> call the API
with the header of the role -> {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}. Every call is written to
the audit log. Differences:
- Role permission is in the API: the sales roles get 403 on the bank, supplier, payment and POS endpoints, and the
  gate turns that into "yetki_yok". The gate has no permission table per tool (writing a manual table for generated
  tools would be manual design).
- An unknown argument is rejected at the gate (the generated schemas have no additionalProperties; the model must not
  be able to send anything else, e.g. the role).
- A future date is not rejected at the gate (there is no "gelecek_tarih"); the API does not show the records after
  today anyway.
- 404 (record not found) and 422 (the API's own argument check) become "gecersiz_arguman", and the message is the
  message of the API.
- The "tarih" field of the envelope is the day of the call (today); "kaynak" is empty (the hand-written tools write
  the rows they read).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

import httpx
import jsonschema
from fastmcp import FastMCP

from sayman.tools import scope as scope_module
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import IdentityError, verify_card
from sayman.tools.server import SCOPE_LABELS, MESSAGES, Refusal, _loggable
from sayman.tools.permissions import PermissionTable
from sayman.experiment.fake_api import ApiEnvironment, create_app
from sayman.data.clock import Clock

ROLE_HEADER = "X-Rol"
TYPE_NAMES = {"string": "metin", "integer": "tam sayı", "number": "sayı", "boolean": "doğru/yanlış"}
_FORMAT_CHECKER = jsonschema.FormatChecker()

# fastmcp prints every 4xx response of the API into its own log as a long stack trace; the gate already writes this
# to the audit log.
logging.getLogger("fastmcp").setLevel(logging.CRITICAL)


def assert_read_only(spec: dict) -> None:
    """There must be no operation other than GET in the API (the "no writing tool" rule of Phase 3)."""
    writers = [f"{y.upper()} {path}" for path, operations in spec.get("paths", {}).items() for y in operations
             if y.lower() not in ("get", "parameters")]
    if writers:
        raise RuntimeError("sahte API'de yazan uç nokta var: " + ", ".join(writers))


def _flatten_any_of(o: dict) -> dict:
    """anyOf [X, null] -> X (only for the error message)."""
    branch = next((a for a in o.get("anyOf", []) if a.get("type") != "null"), None)
    return o if branch is None else {**{k: v for k, v in o.items() if k != "anyOf"}, **branch}


def _format_number(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def argument_message(error: jsonschema.ValidationError, schema: dict) -> str:
    """Describes a schema error using only the names in the schema (the same language as the messages at the gate of
    the hand-written tools)."""
    properties = schema.get("properties", {})
    v = error.validator
    if v == "additionalProperties":
        return "Bilinmeyen argüman. Geçerli argümanlar: " + ", ".join(properties) + "."
    if v == "required":
        return "Eksik argüman: " + ", ".join(a for a in schema.get("required", []) if a not in error.instance) + "."
    field = error.path[0] if error.path and error.path[0] in properties else None
    if field is None:
        return "Argümanlar geçersiz."
    o = _flatten_any_of(properties[field])
    if v == "anyOf":
        inner = next(iter(jsonschema.Draft202012Validator(o, format_checker=_FORMAT_CHECKER).iter_errors(error.instance)), None)
        v = inner.validator if inner is not None else v
    if v == "format":
        return f"{field} YYYY-MM-DD biçiminde geçerli bir tarih olmalı."
    if v == "enum":
        return f"{field} şunlardan biri olmalı: {', '.join(map(str, o['enum']))}."
    if v in ("minimum", "maximum"):
        if "minimum" in o and "maximum" in o:
            return f"{field} {_format_number(o['minimum'])} ile {_format_number(o['maximum'])} arasında olmalı."
        return f"{field} en az {_format_number(o['minimum'])} olmalı." if "minimum" in o else \
            f"{field} en çok {_format_number(o['maximum'])} olmalı."
    if v == "type":
        return f"{field} {TYPE_NAMES.get(o.get('type'), 'geçerli bir değer')} olmalı."
    return f"{field} geçersiz."


def _http_error(e: BaseException) -> httpx.Response | None:
    """The HTTP response of the API in the error chain wrapped by fastmcp."""
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, httpx.HTTPStatusError):
            return e.response
        e = e.__cause__ or e.__context__
    return None


def _api_message(http_response: httpx.Response) -> str:
    try:
        detail = http_response.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, list):  # FastAPI 422: [{"loc": [...], "msg": "..."}]
        return "; ".join(f"{(d.get('loc') or ['?'])[-1]}: {d.get('msg', '')}" for d in detail[:3]) or "Argümanlar geçersiz."
    return str(detail) if detail else f"API {http_response.status_code} döndü."


class GeneratedServer:
    """Given to the Assistant in place of SaymanServer: the same attributes (key, permissions, time_fn, clock) and the
    same gate()."""

    def __init__(self, warehouse: Path, clock: Callable[[], Clock], key: bytes, permissions: PermissionTable,
                 audit_log: AuditLog, scope_dir: Path | None = None, time_fn: Callable[[], float] = time.time):
        if not key:
            raise RuntimeError("imza anahtarı olmadan sunucu açılmaz")
        self.clock, self.key, self.permissions, self.audit_log, self.time_fn = clock, key, permissions, audit_log, time_fn
        directory = Path(scope_dir) if scope_dir else Path(tempfile.mkdtemp(prefix="sayman_kapsam_"))
        roles = {rol: r.scope for rol, r in permissions.roller.items()}
        self.connections = scope_module.connections(warehouse, directory, set(roles.values()))
        self.app = create_app(ApiEnvironment(self.connections, roles, lambda: self.clock().simdi))
        self.spec = self.app.openapi()
        assert_read_only(self.spec)
        self._loop = asyncio.new_event_loop()
        self._clients, self._mcp = [], {}
        for rol in roles:
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://sahte-api",
                                        headers={ROLE_HEADER: rol}, timeout=120)
            self._clients.append(client)
            self._mcp[rol] = FastMCP.from_openapi(openapi_spec=self.spec, client=client, name="sahte-api")
        tools = self._loop.run_until_complete(self._mcp[next(iter(roles))].list_tools())
        self.schemas = {t.name: t.parameters for t in tools}
        self.descriptions = {t.name: t.description or "" for t in tools}
        # The gate rejects an unknown argument; the schema shown to the model does not change.
        self.validators = {name: jsonschema.Draft202012Validator({**s, "additionalProperties": False},
                                                                   format_checker=_FORMAT_CHECKER)
                               for name, s in self.schemas.items()}

    def tool_definitions(self) -> list[dict]:
        """The tool list shown to the model (Ollama format), as fastmcp generates it."""
        return [{"type": "function", "function": {"name": name, "description": self.descriptions[name], "parameters": s}}
                for name, s in self.schemas.items()]

    def digest(self) -> str:
        """Digest of the tool definitions and the OpenAPI definition (written to the measurement rows)."""
        text = json.dumps({"araclar": self.tool_definitions(), "openapi": self.spec}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()

    def gate(self, name, arguments, card) -> tuple[dict, bool]:
        """The single gate. Returns (response, is_error); in every case it writes one row to the audit log."""
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
            if not isinstance(name, str) or name not in self.schemas:
                raise Refusal("bilinmeyen_arac")
            if not isinstance(arguments, dict):
                raise Refusal("gecersiz_arguman", "Argümanlar bir nesne olmalı.")
            error = next(iter(sorted(self.validators[name].iter_errors(arguments), key=lambda e: list(e.path))), None)
            if error is not None:
                raise Refusal("gecersiz_arguman", "Argüman hatası: " + argument_message(error, self.schemas[name]))
            properties = self.schemas[name].get("properties", {})
            arguments = {k: int(v) if isinstance(v, float) and _flatten_any_of(properties[k]).get("type") == "integer" else v
                          for k, v in arguments.items() if v is not None}
            today = self.clock().simdi
            row["simdi"] = today.isoformat()
            scope = self.permissions.roller[identity.role].scope
            try:
                result = self._loop.run_until_complete(self._mcp[identity.role].call_tool(name, arguments))
            except Exception as e:
                http_response = _http_error(e)
                if http_response is None:
                    raise
                row["http"] = http_response.status_code
                if http_response.status_code == 403:
                    raise Refusal("yetki_yok", reason="api_403") from None
                if http_response.status_code in (400, 404, 422):
                    raise Refusal("gecersiz_arguman", _api_message(http_response), reason=f"api_{http_response.status_code}") from None
                raise
            data = result.structured_content
            if not isinstance(data, dict):
                raise RuntimeError("API cevabı bir nesne değil")
            response = {"cagri_id": call_id, "arac": name, "tarih": today.isoformat(), "kapsam": SCOPE_LABELS[scope],
                     "sonuc": data, "kaynak": {}}
            listing = next((v for v in data.values() if isinstance(v, list)), None)
            row.update(sonuc="tamam", tarih=today.isoformat(), kayit_sayisi=len(listing) if listing is not None else 1)
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
        for client in self._clients:
            self._loop.run_until_complete(client.aclose())
        self._loop.close()
        for con in self.connections.values():
            con.close()


IDENTIFIER_TABLES = {"musteri_id": "musteriler", "fatura_id": "faturalar", "iliskili_fatura_id": "faturalar",
                    "tahsilat_id": "tahsilatlar", "cek_id": "cekler", "tedarikci_id": "tedarikciler",
                    "alis_fatura_id": "alis_faturalari", "odeme_id": "odemeler", "pos_id": "pos_islemleri",
                    "hareket_id": "banka_hareketleri", "hesap_id": "banka_hesaplari"}


def source_identifiers(result) -> dict[str, list[str]]:
    """Record identifiers in a generated result, by table (for the scope check; not put in the envelope, does not go
    to the model).

    It corresponds to the "kaynak" field in the envelope of the hand-written tools: the scope check of the scorer
    (`sayman.evaluation.scoring.scope_violations`) looks at "kaynak"; in the generated envelope "kaynak" is empty, so
    the Phase 9 analysis collects the identifiers from the result itself."""
    tables: dict[str, list[str]] = {}

    def walk(v):
        if isinstance(v, dict):
            for k, w in v.items():
                if k in IDENTIFIER_TABLES and isinstance(w, str):
                    tables.setdefault(IDENTIFIER_TABLES[k], []).append(w)
                else:
                    walk(w)
        elif isinstance(v, list):
            for w in v:
                walk(w)

    walk(result)
    return {t: sorted(set(k)) for t, k in tables.items()}


def arm_digest() -> str:
    """Fingerprint of the generated arm: the OpenAPI definition, the fastmcp version and the experiment files (written
    to the measurement rows)."""
    import sys

    import fastmcp

    from sayman.experiment import oracle, fake_api
    spec = create_app(ApiEnvironment({}, {}, lambda: None)).openapi()
    parts = [json.dumps(spec, ensure_ascii=False, sort_keys=True), fastmcp.__version__]
    parts += [Path(m.__file__).read_text(encoding="utf-8") for m in (fake_api, sys.modules[__name__], oracle)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()
