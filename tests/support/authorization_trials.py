"""60 unauthorized trials and the judge that evaluates them (reused with the real model in Phase 7).

The trial table is built from data: another manager's customers and invoices are picked from that day's data. Each
trial expects one of:
- ("ret", {codes}): the call is refused; the response contains only `cagri_id`, `hata` and `mesaj`.
- ("kapsamli",): the call is allowed but asks for something out of scope (another manager's customer, their invoice).
  The response must be exactly what the same tool returns on a warehouse built separately in Python from the in-scope
  rows only (independent of the SQL filter in `tools/scope.py`) and must not contain any out-of-scope text value
  (identifier, name, invoice number...).

Groups: A forbidden tool (12), B forged or broken identity (12), C another manager's data (24), D future date (6),
E odd inputs (6). The trials are sent from a real MCP client session.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from sayman.warehouse.loader import build_warehouse
from sayman.tools import identity
from sayman.tools.catalog import TOOLS_BY_NAME, ToolError, Context
from sayman.data.table_io import write_csv

USERS = {"cfo": "selin.aydin", "yurtici": "murat.kaya", "ihracat": "deniz.arslan"}
FORBIDDEN_TOOLS = ("nakit_pozisyonu", "banka_hareketleri", "yaklasan_odemeler", "nakit_akis_tahmini", "kur_riski",
                 "pos_tahsilatlari")
# Identity attack -> reason expected in the audit log. rol_iddiasi: the card is valid and correctly signed but the
# payload says "rol": "cfo"; the role is not read from the card, so the user stays a sales manager and gets yetki_yok.
IDENTITY_ATTACKS = {
    "yok": "kart_yok", "bos": "kart_bicimi", "bozuk": "kart_bicimi", "yanlis_anahtar": "imza_gecersiz",
    "degistirilmis": "imza_gecersiz", "imzasiz": "imza_gecersiz", "suresi_dolmus": "kart_suresi_dolmus",
    "bilinmeyen_kullanici": "kullanici_bilinmiyor", "cok_uzun_sure": "kart_suresi_cok_uzun",
    "metin_degil": "kart_bicimi", "kesik": "imza_gecersiz", "rol_iddiasi": "yetki_yok",
}
SCOPES = ("yurtici", "ihracat")
CUSTOMER_TABLES = ("faturalar", "tahsilatlar", "cekler")
BARE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------- trial table


def _valid_args(tool: str, today: dt.date) -> dict:
    before = (today - dt.timedelta(days=30)).isoformat()
    return {"nakit_pozisyonu": {}, "banka_hareketleri": {"baslangic": before, "bitis": today.isoformat()},
            "yaklasan_odemeler": {"gun_sayisi": 30}, "nakit_akis_tahmini": {"hafta_sayisi": 4}, "kur_riski": {},
            "pos_tahsilatlari": {"baslangic": before, "bitis": today.isoformat()}}[tool]


def out_of_scope(T: dict, scope: str, today: dt.date) -> dict:
    """Customers and invoices, visible up to `today`, that a sales manager must not see."""
    domestic = scope == "yurtici"
    customers = sorted((m for m in T["musteriler"] if m["yurtici"] != domestic and m["ilk_islem_tarihi"] <= today),
                        key=lambda m: m["musteri_id"])
    ids = {m["musteri_id"] for m in customers}
    invoices = sorted((f for f in T["faturalar"] if f["musteri_id"] in ids and f["fatura_tarihi"] <= today),
                       key=lambda f: f["fatura_id"])
    return {"musteriler": customers, "faturalar": invoices}


def _partial_name(name: str) -> str:
    words = name.split()
    return " ".join(words[:2]) if len(words) > 2 else words[0] if len(words[0]) >= 3 else name


def trials(T: dict, today: dt.date, data_end: dt.date) -> list[dict]:
    if data_end <= today:
        raise ValueError("gelecek tarih denemeleri için veri bitişi şimdiden sonra olmalı")
    trial_list = []

    def add(group, kullanici, tool, arguments, expected, card="gecerli"):
        trial_list.append({"no": len(trial_list) + 1, "grup": group, "kullanici": kullanici, "kart": card, "arac": tool,
                      "argumanlar": arguments, "beklenen": expected})

    # A: sales managers call forbidden tools (6 x 2)
    for scope in SCOPES:
        for tool in FORBIDDEN_TOOLS:
            add("A", USERS[scope], tool, _valid_args(tool, today), ("ret", {"yetki_yok"}))

    # B: identity attacks; all of them ask for CFO data (cash)
    for attack, reason in IDENTITY_ATTACKS.items():
        kullanici = USERS["yurtici"] if attack in ("degistirilmis", "rol_iddiasi") else USERS["cfo"]
        code = "yetki_yok" if reason == "yetki_yok" else "kimlik_gecersiz"
        add("B", kullanici, "nakit_pozisyonu", {}, ("ret", {code}), card=attack)

    # C: another manager's customer and invoices (12 x 2)
    for scope in SCOPES:
        outside = out_of_scope(T, scope, today)
        m, f = outside["musteriler"], outside["faturalar"]
        if not m or not f:
            raise ValueError(f"{scope} için kapsam dışı müşteri ya da fatura yok; denemeler kurulamaz")
        names = [m[i * len(m) // 3]["ad"] for i in range(3)]
        for tool, args in [
            ("musteri_ozeti", {"musteri_adi": names[0]}),
            ("musteri_ozeti", {"musteri_adi": _partial_name(names[1])}),
            ("musteri_ozeti", {"musteri_adi": m[-1]["musteri_id"]}),
            ("alacak_yaslandirma", {"musteri_adi": names[2]}),
            ("alacak_yaslandirma", {"musteri_adi": m[0]["musteri_id"].lower()}),
            ("fatura_ara", {"fatura_no": f[0]["fatura_id"]}),
            ("fatura_ara", {"fatura_no": f[-1]["fatura_id"].lower()}),
            ("fatura_ara", {"fatura_no": f[len(f) // 2]["kaynak_fatura_no"]}),
            ("fatura_ara", {"musteri_adi": names[0], "durum": "hepsi", "en_fazla": 10}),
            ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}),
            ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
            ("dso_hesapla", {"donem": "son_12_ay"}),
        ]:
            add("C", USERS[scope], tool, args, ("kapsamli",))

    # D: future date (end of the range included)
    def day(n):
        return (today + dt.timedelta(days=n)).isoformat()

    for kullanici, tool, args in [
        (USERS["cfo"], "nakit_pozisyonu", {"tarih": day(1)}),
        (USERS["cfo"], "banka_hareketleri", {"baslangic": today.isoformat(), "bitis": day(30)}),
        (USERS["yurtici"], "alacak_yaslandirma", {"tarih": day(7)}),
        (USERS["ihracat"], "fatura_ara", {"tarih": data_end.isoformat()}),
        (USERS["cfo"], "musteri_ozeti", {"musteri_adi": "abc", "tarih": day(1)}),
        (USERS["yurtici"], "doviz_kuru", {"para_birimi": "USD", "tarih": day(1)}),
    ]:
        add("D", kullanici, tool, args, ("ret", {"gelecek_tarih"}))

    # E: odd inputs
    add("E", USERS["yurtici"], "musteri_ozeti", {"musteri_adi": "' OR 1=1 --"}, ("kapsamli",))
    add("E", USERS["ihracat"], "fatura_ara", {"fatura_no": "F1' OR '1'='1"}, ("kapsamli",))
    add("E", USERS["cfo"], "banka_hareketleri",
         {"baslangic": today.isoformat(), "bitis": today.isoformat(), "sql": "DROP TABLE faturalar"},
         ("ret", {"gecersiz_arguman"}))
    add("E", USERS["yurtici"], "alacak_yaslandirma", {"en_fazla": 100000}, ("ret", {"gecersiz_arguman"}))
    add("E", USERS["ihracat"], "fatura_ara", {"musteri_adi": "%"}, ("ret", {"gecersiz_arguman"}))
    add("E", USERS["cfo"], "banka_transferi", {"tutar": "1000000.00"}, ("ret", {"bilinmeyen_arac"}))
    assert len(trial_list) == 60, len(trial_list)
    return trial_list


def widest_calls(today: dt.date, most_invoices: str) -> list[tuple[str, dict]]:
    """Calls that give each tool its longest answer (`en_fazla`=10, widest range); `fatura_ara` also once with the
    defaults."""
    start = "2024-01-01"
    return [("nakit_pozisyonu", {}), ("banka_hareketleri", {"baslangic": start, "bitis": today.isoformat(), "en_fazla": 10}),
            ("alacak_yaslandirma", {"en_fazla": 10}), ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}),
            ("musteri_ozeti", {"musteri_adi": most_invoices}), ("dso_hesapla", {"donem": "son_12_ay"}),
            ("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}), ("nakit_akis_tahmini", {"hafta_sayisi": 13}),
            ("kur_riski", {}), ("doviz_kuru", {"para_birimi": "EUR"}),
            ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
            ("pos_tahsilatlari", {"baslangic": start, "bitis": today.isoformat()}),
            ("fatura_ara", {"durum": "hepsi", "en_fazla": 10}), ("fatura_ara", {"durum": "hepsi"})]


# ---------------------------------------------------------------- identity cards


def _signed(content: dict, key: bytes) -> str:
    payload = identity._b64(json.dumps(content, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{identity._signature(key, payload)}"


def prepare_card(attack: str, kullanici: str, key: bytes, timestamp: float):
    """(send_it, card). 'gecerli' produces the valid card, the others produce the named attack."""
    expiry = int(timestamp) + 3600
    if attack == "gecerli":
        return True, identity.generate_card(kullanici, key, 3600, timestamp)
    if attack == "yok":
        return False, None
    card = {
        "bos": lambda: "",
        "bozuk": lambda: "bu-bir-kart-degil",
        "yanlis_anahtar": lambda: identity.generate_card(kullanici, hashlib.sha256(key).digest(), 3600, timestamp),
        # changes the payload of its own card to CFO and leaves the signature as it is
        "degistirilmis": lambda: _signed({"v": 1, "k": USERS["cfo"], "son": expiry}, b"x").split(".")[0] + "."
        + identity.generate_card(kullanici, key, 3600, timestamp).split(".")[1],
        "imzasiz": lambda: _signed({"v": 1, "k": kullanici, "son": expiry}, key).split(".")[0] + ".",
        "suresi_dolmus": lambda: identity.generate_card(kullanici, key, 3600, timestamp - 7200),
        "bilinmeyen_kullanici": lambda: identity.generate_card("ali.veli", key, 3600, timestamp),
        "cok_uzun_sure": lambda: _signed({"v": 1, "k": kullanici, "son": int(timestamp) + 30 * 86400}, key),
        "metin_degil": lambda: 12345,
        "kesik": lambda: identity.generate_card(kullanici, key, 3600, timestamp)[:-6],
        "rol_iddiasi": lambda: _signed({"v": 1, "k": kullanici, "son": expiry, "rol": "cfo"}, key),
    }[attack]()
    return True, card


# ---------------------------------------------------------------- MCP client


async def _in_session(server, calls):
    output = []
    async with create_connected_server_and_client_session(server.mcp) as session:
        for name, args, send_card, card in calls:
            meta = {identity.META_KEY: card} if send_card else None
            r = await session.call_tool(name, args, meta=meta)
            text = json.loads(r.content[0].text)
            if text != r.structuredContent:
                raise AssertionError("metin içerik ile yapısal içerik farklı")
            output.append((r.structuredContent, bool(r.isError)))
    return output


def call_mcp(server, calls) -> list[tuple[dict, bool]]:
    """calls: (tool, arguments, send_card, card). Sent from a single MCP client session."""
    return anyio.run(_in_session, server, calls)


# ---------------------------------------------------------------- judge


def scope_tables(T: dict, scope: str) -> dict:
    """The in-scope rows, filtered in Python (independent of the SQL filter in `tools/scope.py`)."""
    domestic = scope == "yurtici"
    customers = [m for m in T["musteriler"] if bool(m["yurtici"]) == domestic]
    ids = {m["musteri_id"] for m in customers}
    result = {name: [] for name in T}
    result["musteriler"] = customers
    for name in CUSTOMER_TABLES:
        result[name] = [s for s in T[name] if s["musteri_id"] in ids]
    cheques = {c["cek_id"] for c in result["cekler"]}
    result["cek_hareketleri"] = [h for h in T["cek_hareketleri"] if h["cek_id"] in cheques]
    result["doviz_kurlari"] = list(T["doviz_kurlari"])
    return result


def table_texts(T: dict) -> set[str]:
    return {v for rows in T.values() for s in rows for v in s.values() if isinstance(v, str)}


def text_values(obj) -> set[str]:
    if isinstance(obj, str):
        return {obj}
    if isinstance(obj, dict):
        return set().union(*(text_values(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(text_values(v) for v in obj))
    return set()


class Judge:
    """Evaluates responses: reference warehouses built separately from the in-scope rows, and forbidden values."""

    def __init__(self, T: dict, today: dt.date, directory: Path):
        self.simdi = today
        self.reference_warehouses, self.forbidden_values = {}, {}
        all_texts = table_texts(T)
        for scope in SCOPES:
            tables = scope_tables(T, scope)
            target = Path(directory) / f"referans_{scope}"
            target.mkdir(parents=True, exist_ok=True)
            write_csv(tables, target)
            self.reference_warehouses[scope] = build_warehouse(target)
            # Out-of-scope texts; those that also occur in scope (e.g. "EUR", a shared bank name) are removed. Bare dates are
            # removed too (the source_id of some bank transactions is a day): a day identifies nobody, and if anything leaks
            # through a date, the comparison with the reference warehouse catches it anyway.
            self.forbidden_values[scope] = {v for v in all_texts - table_texts(tables) if not BARE_DATE.fullmatch(v)}
        self.hepsi = all_texts

    def expected_response(self, scope: str, tool: str, args: dict) -> dict:
        """The same tool's response on the reference warehouse. If the tool raises an input error, the expected result is
        that error."""
        try:
            date, result, source = TOOLS_BY_NAME[tool].run(Context(self.reference_warehouses[scope], self.simdi), args)
        except ToolError as e:
            return {"hata": "gecersiz_arguman", "mesaj": str(e)}
        return json.loads(json.dumps({"tarih": date.isoformat(), "sonuc": result, "kaynak": source},
                                     ensure_ascii=False))

    def evaluate_response(self, trial: dict, scope: str | None, response: dict, error: bool,
                    exclude_echoed: bool = False) -> list[str]:
        """exclude_echoed: values the user typed themselves (e.g. durum="portfoyde") do not count as leaks if they appear
        in the response. Off in the 60 trials; none of the trials gets a response that echoes its input."""
        problems = []
        if trial["beklenen"][0] == "ret":
            if not error or set(response) != {"cagri_id", "hata", "mesaj"}:
                problems.append("reddedilmedi ya da ret cevabında fazladan alan var")
            elif response["hata"] not in trial["beklenen"][1]:
                problems.append(f"beklenmeyen ret kodu: {response['hata']}")
            forbidden = self.hepsi
        else:
            expected = self.expected_response(scope, trial["arac"], trial["argumanlar"])
            fields = ("hata", "mesaj") if "hata" in expected else ("tarih", "sonuc", "kaynak")
            if error != ("hata" in expected) or {k: response.get(k) for k in fields} != expected:
                problems.append("yalnızca kapsamdaki satırlarla kurulan ambarın cevabından farklı")
            forbidden = self.forbidden_values[scope]
        leaked = text_values(response) & forbidden
        if exclude_echoed:
            leaked -= text_values(trial["argumanlar"])
        if leaked:
            problems.append(f"kapsam dışı değer: {sorted(leaked)[:5]}")
        return problems

    def close(self):
        for con in self.reference_warehouses.values():
            con.close()


def run_trials(server, trial_list: list[dict], key: bytes, timestamp: float, judge: Judge) -> list[dict]:
    """Sends the trials over MCP; for each trial checks the response, the problems and the audit record."""
    cards = [prepare_card(d["kart"], d["kullanici"], key, timestamp) for d in trial_list]
    before = len(server.audit_log.rows)
    responses = call_mcp(server, [(d["arac"], d["argumanlar"], g, k) for d, (g, k) in zip(trial_list, cards)])
    records = server.audit_log.rows[before:]
    results = []
    for d, (send_card, card), (response, error), row in zip(trial_list, cards, responses, records):
        scope = None
        if d["kart"] == "gecerli" or d["kart"] == "rol_iddiasi":
            scope = server.permissions.roller[server.permissions.users[d["kullanici"]]].scope
        problems = judge.evaluate_response(d, scope, response, error)
        if row.get("cagri_id") != response.get("cagri_id"):
            problems.append("denetim kaydındaki çağrı bu çağrı değil")
        expected_outcome = "reddedildi" if error else "tamam"  # the judge checked separately whether the response is correct
        if row.get("sonuc") != expected_outcome:
            problems.append(f"denetim kaydında sonuç {row.get('sonuc')}")
        if d["grup"] == "B" and row.get("neden") != IDENTITY_ATTACKS[d["kart"]]:
            problems.append(f"denetim kaydında neden {row.get('neden')}")
        if isinstance(card, str) and len(card) > 8 and card in json.dumps(row, ensure_ascii=False):
            problems.append("kimlik kartı denetim kaydına yazılmış")
        results.append({
            "no": d["no"], "grup": d["grup"], "kullanici": d["kullanici"], "kart": d["kart"], "arac": d["arac"],
            "argumanlar": d["argumanlar"],
            "beklenen": "kapsamli" if d["beklenen"][0] == "kapsamli" else "ret: " + "/".join(sorted(d["beklenen"][1])),
            "cevap": f"ret: {response['hata']}" if error else "tamam", "sorunlar": problems})
    if len(records) != len(trial_list):
        raise AssertionError(f"{len(trial_list)} çağrı için {len(records)} denetim satırı")

    # Group C must not contain empty trials: if the answer differs when the same question is asked as the CFO, something
    # really was hidden from the manager ("gizledi"). If it is the same, that trial touched no data worth hiding.
    group_c = [(d, response) for d, (response, _) in zip(trial_list, responses) if d["grup"] == "C"]
    cfo_card = identity.generate_card(USERS["cfo"], key, 3600, timestamp)
    cfo = call_mcp(server, [(d["arac"], d["argumanlar"], True, cfo_card) for d, _ in group_c])

    def body(y):
        return {k: v for k, v in y.items() if k not in ("cagri_id", "kapsam")}

    hid = {d["no"]: body(y) != body(c) for (d, y), (c, _) in zip(group_c, cfo)}
    for r in results:
        if r["no"] in hid:
            r["gizledi"] = hid[r["no"]]
    return results
