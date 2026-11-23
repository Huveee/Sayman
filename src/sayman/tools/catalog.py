"""The 14 read-only tools the model calls.

Each tool uses Phase 2 metrics or fixed, parameterised SELECT queries; none writes. Tools do not know about
permissions: they run on the connection given to them (scope-limited, read-only). Identity, permission and date
checks and the audit log are done at the server's single gate (server.py).

belge_ara, added in Phase 6, looks at the Phase 5 document library, not the warehouse. For documents the scope is
a role, not a connection: the searcher never takes a chunk the role cannot see into the ranking. The default
method is semantic search; the reranker ("karma_sirala") is a server setting.

Output format:
- Amounts are text, with a decimal point and two digits: "-1234.56". TL fields have names ending in "_tl";
  foreign-currency amounts are accompanied by "para_birimi". Floating-point numbers are not used.
- Lists have at most `en_fazla` (1-10, default 5) rows; truncated lists always give the total count and total
  amount.
- "kaynak": the identifiers of the rows shown (table -> list of identifiers). The verifier (Phase 6) matches the
  numbers in the answer against them. Only the four tools that give just totals (AGGREGATE_TOOLS) have an empty
  source: these answers are cited by the `cagri_id` the server gives; since the audit log holds the tool, the
  arguments, the role and the simulation day (`simdi`), the same call can be run again to get the same result.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from sayman.warehouse import metrics
from sayman.warehouse.loader import query_rows

# The list size is based on measurement: on qwen3.5:9b a 20-row invoice list took 3,390 tokens (together with the
# tool list only one fit in the 8,192 window). 10 rows is ~1,750 tokens. The total count and amount are always given in full.
LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": 10, "description": "En fazla kaç satır listelensin (varsayılan 5)"}
DATE_SCHEMA = {"type": "string", "format": "date", "description": "Hangi gün itibarıyla, YYYY-MM-DD. Verilmezse bugün."}
NAME_SCHEMA = {"type": "string", "maxLength": 100}
ID_SCHEMA = {"type": "string", "maxLength": 40}
PERIODS = {"son_30_gun": 30, "son_90_gun": 90, "son_12_ay": 365}
# Document search: article texts have a median of 277, 90% up to 886 and 99% up to 2,249 characters (Phase 5
# library, 3,003 chunks; contract and policy articles are at most 199 characters, the long ones are law articles).
# An article longer than 2,500 characters (0.8%) is cut at a word boundary. 1,200 was tried first: 5.1% were cut and
# in the smoke test the phrase "on yıl" of TTK art. 82 (at character 1,413) was cut off and lost. At most 3 results
# ≈ 7,500 characters; in the Phase 6 measurement article text is ~3.1 characters/token, i.e. ~2,400 tokens:
# together with the tool list (~2,340) and the system prompt it fits in the 8,192 window.
DOCUMENT_DEFAULT_LIMIT = 3
DOCUMENT_MAX_LIMIT = 3
DOCUMENT_TEXT_LIMIT = 2500
CHEQUE_STATUSES = {"bekleyen": ("portfoyde", "tahsilde"), "portfoyde": ("portfoyde",), "tahsilde": ("tahsilde",),
                 "karsiliksiz": ("karsiliksiz",), "tahsil_edildi": ("tahsil_edildi",),
                 "hepsi": ("portfoyde", "tahsilde", "karsiliksiz", "tahsil_edildi")}


class ToolError(Exception):
    """An input error the user can fix (e.g. the start is after the end)."""


class ToolUnavailable(Exception):
    """The tool cannot run in this setup (e.g. the document library is not loaded)."""


@dataclass
class Context:
    con: object
    simdi: dt.date
    role: str | None = None          # used only by belge_ara: for documents the scope is a role, not a connection
    belge: object | None = None     # document searcher (from sayman.documents.search, or anything with the same ara() method)
    search_method: str = "anlam"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict
    run: Callable[[Context, dict], tuple[dt.date, dict, dict]] = field(repr=False)
    required: tuple[str, ...] = ()

    @property
    def schema(self) -> dict:
        return {"type": "object", "properties": self.properties, "required": list(self.required),
                "additionalProperties": False}

    @property
    def date_fields(self) -> tuple[str, ...]:
        return tuple(a for a, s in self.properties.items() if s.get("format") == "date")


# ---------------------------------------------------------------- helpers


def lira_text(cents) -> str:
    k = int(cents)
    return f"{'-' if k < 0 else ''}{abs(k) // 100}.{abs(k) % 100:02d}"


def rate_text(rate) -> str:
    return format(Decimal(rate).normalize(), "f")


def _date_argument(b: Context, a: dict, field_name: str = "tarih") -> dt.date:
    return dt.date.fromisoformat(a[field_name]) if a.get(field_name) else b.simdi


def _row_limit(a: dict) -> int:
    return a.get("en_fazla", 5)


def _normalize(text: str) -> str:
    return " ".join(text.replace("İ", "i").replace("I", "ı").lower().split())


def find_customer(b: Context, name: str, day: dt.date) -> list[dict]:
    """Customer by name (or customer id): first an exact match, otherwise names that contain it (at least 3
    letters). Only customers that had a transaction by that day and are in the connection's scope are searched."""
    wanted = _normalize(name)
    candidates = query_rows(b.con, "SELECT musteri_id, ad, ulke, para_birimi, vade_gun FROM musteriler "
                              "WHERE ilk_islem_tarihi <= $d ORDER BY ad, musteri_id", {"d": day})
    exact = [m for m in candidates if _normalize(m["ad"]) == wanted or m["musteri_id"].lower() == wanted]
    if exact:
        return exact
    if len(wanted) < 3:
        raise ToolError("Müşteri adı en az 3 harf olmalı.")
    return [m for m in candidates if wanted in _normalize(m["ad"])]


def _customer_disambiguation(matches: list[dict]):
    """None if exactly one customer was found; otherwise the explanation that goes back to the model."""
    if not matches:
        return {"bulunamadi": True, "mesaj": "Bu adla bir müşteri bulunamadı."}
    if len(matches) > 1:
        return {"birden_fazla": True, "aday_sayisi": len(matches), "adaylar": [m["ad"] for m in matches[:5]],
                "mesaj": "Birden fazla müşteri eşleşti; müşterinin tam adını yazın."}
    return None


def _aging_buckets(row: dict | None) -> dict:
    fields = [f"{name}_kurus" for name, _, _ in metrics.BUCKETS] + ["eslesmemis_tahsilat_kurus"]
    return {a.removesuffix("_kurus"): lira_text((row or {}).get(a, 0)) for a in fields}


def _overdue_total(row: dict) -> int:
    return sum(row[f"{name}_kurus"] for name, min_days, _ in metrics.BUCKETS if min_days is not None)


# ---------------------------------------------------------------- tools


def _cash_position(b, a):
    d = _date_argument(b, a)
    m = metrics.cash_position(b.con, d)
    accounts = [{"hesap_id": h["hesap_id"], "banka": h["banka"], "para_birimi": h["para_birimi"],
                 "bakiye": lira_text(h["bakiye_kurus"]), "kur": rate_text(h["kur"]), "bakiye_tl": lira_text(h["bakiye_try_kurus"])}
                for h in m["hesaplar"]]
    return d, {"hesaplar": accounts, "toplam_tl": lira_text(m["toplam_try_kurus"])}, \
        {"banka_hesaplari": [h["hesap_id"] for h in m["hesaplar"]]}


def _bank_transactions(b, a):
    start, end = _date_argument(b, a, "baslangic"), _date_argument(b, a, "bitis")
    if start > end:
        raise ToolError("Başlangıç günü bitiş gününden sonra olamaz.")
    conditions = ["h.tarih BETWEEN $bas AND $bit"]
    p = {"bas": start, "bit": end}
    if a.get("hesap_id"):
        conditions.append("h.hesap_id = $hesap")
        p["hesap"] = a["hesap_id"]
    direction = a.get("yon", "hepsi")
    if direction == "giris":
        conditions.append("h.tutar_kurus > 0")
    elif direction == "cikis":
        conditions.append("h.tutar_kurus < 0")
    where_clause = " AND ".join(conditions)
    summary = query_rows(b.con, f"""
        SELECT k.para_birimi, count(*) AS adet, sum(greatest(h.tutar_kurus, 0)) AS giris,
               sum(least(h.tutar_kurus, 0)) AS cikis
        FROM banka_hareketleri h JOIN banka_hesaplari k ON k.hesap_id = h.hesap_id
        WHERE {where_clause} GROUP BY k.para_birimi ORDER BY k.para_birimi""", p)
    rows = query_rows(b.con, f"""
        SELECT h.hareket_id, h.hesap_id, k.para_birimi, h.tarih, h.tutar_kurus, h.aciklama
        FROM banka_hareketleri h JOIN banka_hesaplari k ON k.hesap_id = h.hesap_id
        WHERE {where_clause} ORDER BY h.tarih DESC, h.hareket_id DESC LIMIT $n""", {**p, "n": _row_limit(a)})
    result = {
        "baslangic": start.isoformat(), "bitis": end.isoformat(),
        "hareket_sayisi": sum(int(o["adet"]) for o in summary),
        "toplamlar": [{"para_birimi": o["para_birimi"], "adet": int(o["adet"]), "giris": lira_text(o["giris"]),
                       "cikis": lira_text(o["cikis"])} for o in summary],
        "gosterilen": len(rows),
        "hareketler": [{"hareket_id": h["hareket_id"], "hesap_id": h["hesap_id"], "tarih": h["tarih"].isoformat(),
                        "tutar": lira_text(h["tutar_kurus"]), "para_birimi": h["para_birimi"], "aciklama": h["aciklama"]}
                       for h in rows],
    }
    return end, result, {"banka_hareketleri": [h["hareket_id"] for h in rows]}


def _receivables_aging(b, a):
    d = _date_argument(b, a)
    y = metrics.aging(b.con, d)
    if a.get("musteri_adi"):
        matches = find_customer(b, a["musteri_adi"], d)
        explanation = _customer_disambiguation(matches)
        if explanation:
            return d, explanation, {}
        m = matches[0]
        row = next((s for s in y["musteriler"] if s["musteri_id"] == m["musteri_id"]), None)
        return d, {"musteri_id": m["musteri_id"], "musteri": m["ad"], "dilimler": _aging_buckets(row),
                   "toplam_tl": lira_text(row["toplam_kurus"] if row else 0)}, {"musteriler": [m["musteri_id"]]}
    largest = y["musteriler"][:_row_limit(a)]
    result = {
        "dilimler": _aging_buckets(y["toplam"]), "toplam_tl": lira_text(y["toplam"]["toplam_kurus"]),
        "vadesi_gecmis_tl": lira_text(_overdue_total(y["toplam"])), "musteri_sayisi": len(y["musteriler"]),
        "en_buyuk_alacaklar": [{"musteri_id": s["musteri_id"], "musteri": s["ad"], "toplam_tl": lira_text(s["toplam_kurus"]),
                                "vadesi_gecmis_tl": lira_text(_overdue_total(s))} for s in largest],
        "not": "TL tutarlar fatura günündeki kurla; eşleşmemiş tahsilatlar alacaktan düşülmüştür.",
    }
    return d, result, {"musteriler": [s["musteri_id"] for s in largest]}


def _overdue_receivables(b, a):
    d = _date_argument(b, a)
    g = metrics.overdue_receivables(b.con, d, en_az_gun=a["min_gun"])
    shown = g["faturalar"][:_row_limit(a)]
    result = {
        "min_gun": a["min_gun"], "fatura_sayisi": len(g["faturalar"]), "toplam_tl": lira_text(g["toplam_try_kurus"]),
        "faturalar": [{"fatura_id": f["fatura_id"], "musteri_id": f["musteri_id"], "musteri": f["ad"],
                       "vade_tarihi": f["vade_tarihi"].isoformat(), "gecikme_gun": f["gecikme_gun"],
                       "acik_tutar": lira_text(f["acik_kurus"]), "para_birimi": f["para_birimi"],
                       "acik_tutar_tl": lira_text(f["acik_try_kurus"])} for f in shown],
    }
    return d, result, {"faturalar": [f["fatura_id"] for f in shown]}


def _customer_summary(b, a):
    d = _date_argument(b, a)
    matches = find_customer(b, a["musteri_adi"], d)
    explanation = _customer_disambiguation(matches)
    if explanation:
        return d, explanation, {}
    m = matches[0]
    customer_id = m["musteri_id"]
    aging_row = next((s for s in metrics.aging(b.con, d)["musteriler"] if s["musteri_id"] == customer_id), None)
    open_invoices = [f for f in metrics.overdue_receivables(b.con, d, en_az_gun=-10**6)["faturalar"] if f["musteri_id"] == customer_id]
    collection_rows = query_rows(b.con, """
        SELECT tahsilat_id, tarih, tutar_kurus, para_birimi, yontem, fatura_id FROM tahsilatlar
        WHERE musteri_id = $m AND tarih <= $d ORDER BY tarih DESC, tahsilat_id DESC LIMIT 5""", {"m": customer_id, "d": d})
    pending_cheques = query_rows(b.con, """
        WITH son AS (
            SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS s
            FROM cek_hareketleri WHERE tarih <= $d)
        SELECT count(*) AS adet, coalesce(sum(c.tutar_kurus), 0) AS toplam
        FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
        WHERE c.musteri_id = $m AND c.alis_tarihi <= $d AND son.durum IN ('portfoyde', 'tahsilde')""",
                    {"m": customer_id, "d": d})[0]
    history = metrics.payment_history(b.con, d)["musteriler"].get(customer_id)
    result = {
        "musteri_id": customer_id, "musteri": m["ad"], "ulke": m["ulke"], "para_birimi": m["para_birimi"],
        "vade_gun": m["vade_gun"],
        "acik_alacak_tl": lira_text(aging_row["toplam_kurus"] if aging_row else 0), "dilimler": _aging_buckets(aging_row),
        "acik_fatura_sayisi": len(open_invoices),
        "en_eski_acik_vade": min((f["vade_tarihi"] for f in open_invoices), default=None),
        "son_tahsilatlar": [{"tahsilat_id": t["tahsilat_id"], "tarih": t["tarih"].isoformat(),
                             "tutar": lira_text(t["tutar_kurus"]), "para_birimi": t["para_birimi"], "yontem": t["yontem"],
                             "fatura_id": t["fatura_id"]} for t in collection_rows],
        "bekleyen_cek_sayisi": int(pending_cheques["adet"]), "bekleyen_cek_tutari": lira_text(pending_cheques["toplam"]),
        "son_12_ay_kapanan_fatura": history["fatura_sayisi"] if history else 0,
        "son_12_ay_gec_kapanan_fatura": history["gec_kapanan"] if history else 0,
        "son_12_ay_ortalama_gecikme_gun": history["ortalama_gecikme_gun"] if history else None,
    }
    if result["en_eski_acik_vade"]:
        result["en_eski_acik_vade"] = result["en_eski_acik_vade"].isoformat()
    return d, result, {"musteriler": [customer_id], "tahsilatlar": [t["tahsilat_id"] for t in collection_rows]}


def _compute_dso(b, a):
    d = _date_argument(b, a)
    day = PERIODS[a["donem"]]
    m = metrics.dso(b.con, d, day)
    result = {
        "donem": a["donem"], "gun": day, "pencere_baslangic": (d - dt.timedelta(days=day - 1)).isoformat(),
        "pencere_bitis": d.isoformat(), "dso_gun": None if m["dso_gun"] is None else str(m["dso_gun"]),
        "acik_alacak_tl": lira_text(m["alacak_kurus"]), "donem_satisi_tl": lira_text(m["satis_kurus"]),
        "formul": "DSO = gün sayısı x açık alacak / dönemdeki net satış (iadeler düşülmüş). Kapsam tüm şirketse "
                  "POS satışları da dahildir.",
    }
    if m["dso_gun"] is None:
        result["mesaj"] = "Bu dönemde satış olmadığı için DSO hesaplanamadı."
    return d, result, {}


def _upcoming_payments(b, a):
    d = b.simdi
    y = metrics.upcoming_payments(b.con, d, a["gun_sayisi"])
    shown = y["odemeler"][:_row_limit(a)]
    result = {
        "gun_sayisi": a["gun_sayisi"], "fatura_sayisi": len(y["odemeler"]), "toplam_tl": lira_text(y["toplam_kurus"]),
        "vadesi_gecmis_tl": lira_text(sum(o["acik_kurus"] for o in y["odemeler"] if o["kalan_gun"] < 0)),
        "odemeler": [{"alis_fatura_id": o["alis_fatura_id"], "tedarikci": o["ad"], "kategori": o["kategori"],
                      "vade_tarihi": o["vade_tarihi"].isoformat(), "kalan_gun": o["kalan_gun"],
                      "tutar_tl": lira_text(o["acik_kurus"])} for o in shown],
        "not": "Ödenmemiş tedarikçi (alış) faturalarıdır; maaş, SGK ve vergi ödemeleri dahil değildir.",
    }
    return d, result, {"alis_faturalari": [o["alis_fatura_id"] for o in shown]}


def _cash_flow_forecast(b, a):
    d = b.simdi
    n = metrics.cash_flow_forecast(b.con, d, a["hafta_sayisi"])
    weeks = [{"hafta": h["hafta"], "bas": h["bas"].isoformat(), "bit": h["bit"].isoformat(),
                 "giris_tl": lira_text(h["alacak_girisi_kurus"] + h["cek_girisi_kurus"] + h["pos_girisi_kurus"]),
                 "cikis_tl": lira_text(h["tedarikci_cikisi_kurus"]), "net_tl": lira_text(h["net_kurus"]),
                 "bilinen_kalemlerle_bakiye_tl": lira_text(h["bilinen_kalemlerle_bakiye_kurus"])} for h in n["haftalar"]]
    result = {
        "acilis_tl": lira_text(n["acilis_kurus"]), "haftalar": weeks,
        "supheli_alacak_tl": lira_text(n["supheli_alacak_kurus"]), "ufuk_disi_giris_tl": lira_text(n["ufuk_disi_giris_kurus"]),
        "ufuk_disi_cikis_tl": lira_text(n["ufuk_disi_cikis_kurus"]), "dahil_olmayanlar": n["dahil_olmayanlar"],
        "not": "Bu bir bakiye tahmini değildir: yalnızca bilinen kalemler (açık alacaklar, çekler, POS valörleri, "
               "ödenmemiş alış faturaları) vardır. Vadesi geçmiş alacaklar 1. haftaya yazılır; vadesini 90 günden "
               "fazla geçmiş alacaklar şüpheli sayılıp dışarıda bırakılır.",
    }
    return d, result, {}


def _fx_risk(b, a):
    d = _date_argument(b, a)
    k = metrics.fx_risk(b.con, d)
    fx_rows = [x for x in k["dovizler"] if a.get("para_birimi") in (None, x["para_birimi"])]
    result = {
        "dovizler": [{"para_birimi": x["para_birimi"], "alacak": lira_text(x["alacak_doviz_kurus"]),
                      "banka": lira_text(x["banka_doviz_kurus"]), "toplam": lira_text(x["toplam_doviz_kurus"]),
                      "kur": rate_text(x["kur"]), "toplam_tl": lira_text(x["toplam_try_kurus"]),
                      "kur_yuzde_10_degisirse_etki_tl": lira_text(x["yuzde_10_etki_kurus"]),
                      "alacak_kur_farki_tl": lira_text(x["alacak_kur_farki_kurus"])} for x in fx_rows],
        "not": "Alacak ve banka tutarları döviz cinsindendir; TL karşılığı o günün TCMB alış kuruyla.",
    }
    return d, result, {}


def _fx_rate(b, a):
    d = _date_argument(b, a)
    s = query_rows(b.con, "SELECT kur_id, tarih, alis FROM doviz_kurlari WHERE para_birimi = $pb AND tarih <= $d "
                        "ORDER BY tarih DESC LIMIT 1", {"pb": a["para_birimi"], "d": d})
    if not s:
        return d, {"bulunamadi": True, "mesaj": "Bu gün için kur bulunamadı."}, {}
    return d, {"para_birimi": a["para_birimi"], "kur_tarihi": s[0]["tarih"].isoformat(), "alis": rate_text(s[0]["alis"]),
               "not": "TCMB döviz alış kuru; hafta sonu ve tatilde son iş gününün kuru."}, \
        {"doviz_kurlari": [s[0]["kur_id"]]}


def _cheque_portfolio(b, a):
    d = _date_argument(b, a)
    statuses = CHEQUE_STATUSES[a.get("durum", "bekleyen")]
    p = {"d": d, "n": _row_limit(a)}
    due_clause = ""
    if a.get("musteri_adi"):  # Phase 9 third arm: the cheques of a single customer
        matches = find_customer(b, a["musteri_adi"], d)
        explanation = _customer_disambiguation(matches)
        if explanation:
            return d, explanation, {}
        due_clause = " AND c.musteri_id = $m"
        p["m"] = matches[0]["musteri_id"]
    if a.get("vade_gun") is not None:
        due_clause += " AND c.vade_tarihi BETWEEN $d AND CAST($d AS DATE) + CAST($vg AS INTEGER)"
        p["vg"] = a["vade_gun"]
    status_list = ", ".join(f"'{x}'" for x in statuses)  # a fixed set, not user input
    base_query = f"""
        WITH son AS (
            SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS s
            FROM cek_hareketleri WHERE tarih <= $d),
        cek AS (
            SELECT c.cek_id, c.musteri_id, m.ad, c.kesideci_banka, c.vade_tarihi, c.tutar_kurus, son.durum
            FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
            LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
            WHERE c.alis_tarihi <= $d AND son.durum IN ({status_list}){due_clause})"""
    summary = query_rows(b.con, base_query + " SELECT durum, count(*) AS adet, sum(tutar_kurus) AS toplam FROM cek "
                                   "GROUP BY durum ORDER BY durum", {k: v for k, v in p.items() if k != "n"})
    rows = query_rows(b.con, base_query + " SELECT * FROM cek ORDER BY vade_tarihi, cek_id LIMIT $n", p)
    result = {
        "durum": a.get("durum", "bekleyen"), "cek_sayisi": sum(int(o["adet"]) for o in summary),
        "toplam_tl": lira_text(sum(int(o["toplam"]) for o in summary)),
        "durumlara_gore": [{"durum": o["durum"], "adet": int(o["adet"]), "toplam_tl": lira_text(o["toplam"])} for o in summary],
        "cekler": [{"cek_id": c["cek_id"], "musteri": c["ad"], "kesideci_banka": c["kesideci_banka"],
                    "vade_tarihi": c["vade_tarihi"].isoformat(), "tutar_tl": lira_text(c["tutar_kurus"]), "durum": c["durum"]}
                   for c in rows],
    }
    return d, result, {"cekler": [c["cek_id"] for c in rows]}


def _pos_collections(b, a):
    start, end = _date_argument(b, a, "baslangic"), _date_argument(b, a, "bitis")
    if start > end:
        raise ToolError("Başlangıç günü bitiş gününden sonra olamaz.")
    s = query_rows(b.con, """
        SELECT count(*) AS adet, coalesce(sum(CASE WHEN tutar_kurus > 0 THEN tutar_kurus END), 0) AS satis,
               coalesce(sum(CASE WHEN tutar_kurus < 0 THEN tutar_kurus END), 0) AS iade,
               coalesce(sum(CASE WHEN tutar_kurus < 0 THEN 1 ELSE 0 END), 0) AS iade_adet,
               coalesce(sum(komisyon_kurus), 0) AS komisyon,
               coalesce(sum(tutar_kurus - komisyon_kurus), 0) AS net,
               coalesce(sum(CASE WHEN valor_tarihi > $simdi THEN tutar_kurus - komisyon_kurus END), 0) AS bekleyen
        FROM pos_islemleri WHERE islem_tarihi BETWEEN $bas AND $bit""", {"bas": start, "bit": end, "simdi": b.simdi})[0]
    result = {"baslangic": start.isoformat(), "bitis": end.isoformat(), "islem_sayisi": int(s["adet"]),
             "iade_sayisi": int(s["iade_adet"]), "satis_tl": lira_text(s["satis"]), "iade_tl": lira_text(s["iade"]),
             "komisyon_tl": lira_text(s["komisyon"]), "net_tl": lira_text(s["net"]),
             "valoru_gelmemis_net_tl": lira_text(s["bekleyen"])}
    return end, result, {}


def _search_invoices(b, a):
    d = _date_argument(b, a)
    conditions, p = ["f.fatura_tarihi <= $d"], {"d": d, "n": _row_limit(a)}
    if a.get("fatura_no"):
        conditions.append("(lower(f.fatura_id) = lower($no) OR lower(f.kaynak_fatura_no) = lower($no))")
        p["no"] = a["fatura_no"].strip()
    if a.get("musteri_adi"):
        matches = find_customer(b, a["musteri_adi"], d)
        explanation = _customer_disambiguation(matches)
        if explanation:
            return d, explanation, {}
        conditions.append("f.musteri_id = $m")
        p["m"] = matches[0]["musteri_id"]
    status = a.get("durum", "hepsi")
    if status == "acik":
        conditions.append("f.tur = 'satis' AND a.acik_kurus > 0")
    elif status == "kapali":
        conditions.append("f.tur = 'satis' AND a.acik_kurus <= 0")
    elif status == "iade":
        conditions.append("f.tur = 'iade'")
    base_query = f"""
        WITH {metrics.OPEN_INVOICES}
        SELECT f.fatura_id, f.kaynak_fatura_no, f.tur, f.musteri_id, m.ad, f.fatura_tarihi, f.vade_tarihi,
               f.para_birimi, f.tutar_kurus, f.tutar_try_kurus, a.acik_kurus, f.iliskili_fatura_id
        FROM faturalar f LEFT JOIN acik a ON a.fatura_id = f.fatura_id
        LEFT JOIN musteriler m ON m.musteri_id = f.musteri_id
        WHERE {' AND '.join(conditions)}"""
    count = query_rows(b.con, f"SELECT count(*) AS n FROM ({base_query})", {k: v for k, v in p.items() if k != "n"})[0]["n"]
    rows = query_rows(b.con, base_query + " ORDER BY f.fatura_tarihi DESC, f.fatura_id DESC LIMIT $n", p)
    invoices = []
    for f in rows:
        if f["tur"] == "iade":
            invoice_status = "iade"
        else:
            invoice_status = "acik" if f["acik_kurus"] > 0 else "kapali"
        row = {"fatura_id": f["fatura_id"], "kaynak_fatura_no": f["kaynak_fatura_no"], "tur": f["tur"],
                 "musteri": f["ad"], "fatura_tarihi": f["fatura_tarihi"].isoformat(),
                 "vade_tarihi": f["vade_tarihi"].isoformat(), "tutar": lira_text(f["tutar_kurus"]),
                 "para_birimi": f["para_birimi"], "tutar_tl": lira_text(f["tutar_try_kurus"]), "durum": invoice_status}
        if f["tur"] == "satis":
            row["acik_tutar"] = lira_text(max(int(f["acik_kurus"]), 0))
        else:
            row["iliskili_fatura_id"] = f["iliskili_fatura_id"]
        invoices.append(row)
    return d, {"bulunan": int(count), "gosterilen": len(invoices), "faturalar": invoices}, \
        {"faturalar": [f["fatura_id"] for f in invoices]}


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > 0 else limit].rstrip() + " …", True


def _search_documents(b, a):
    if b.belge is None or b.role is None:
        raise ToolUnavailable("Belge araması şu an kullanılamıyor.")
    found = b.belge.search(a["sorgu"], b.role, a.get("en_fazla", DOCUMENT_DEFAULT_LIMIT), b.search_method)
    results = []
    for p, _ in found:
        text, truncated = _truncate(p.metin, DOCUMENT_TEXT_LIMIT)
        s = {"parca_id": p.parca_id, "belge": p.belge_baslik, "madde": p.madde_baslik, "metin": text}
        if truncated:
            s["metin_kesildi"] = True
        results.append(s)
    return b.simdi, {"sorgu": a["sorgu"], "sonuclar": results}, \
        {"belge_parcalari": [s["parca_id"] for s in results]}


# ---------------------------------------------------------------- definitions

TOOLS: list[ToolSpec] = [
    ToolSpec("nakit_pozisyonu", "Bankalardaki nakit: her hesabın bakiyesi ve toplamın TL karşılığı.",
         {"tarih": DATE_SCHEMA}, _cash_position),
    ToolSpec("banka_hareketleri", "Bir tarih aralığındaki banka hareketlerini listeler; para birimine göre giriş ve "
                              "çıkış toplamlarını verir.",
         {"baslangic": {**DATE_SCHEMA, "description": "Aralığın ilk günü, YYYY-MM-DD"},
          "bitis": {**DATE_SCHEMA, "description": "Aralığın son günü, YYYY-MM-DD"},
          "hesap_id": {**ID_SCHEMA, "description": "Tek bir hesap için hesap kimliği"},
          "yon": {"type": "string", "enum": ["giris", "cikis", "hepsi"], "description": "Para girişi mi çıkışı mı"},
          "en_fazla": LIMIT_SCHEMA},
         _bank_transactions, ("baslangic", "bitis")),
    ToolSpec("alacak_yaslandirma", "Açık alacakları vadeden bu yana geçen güne göre dilimlere ayırır (vadesi gelmemiş, "
                               "1-30, 31-60, 61-90, 91-180, 180 gün üstü); en büyük alacaklı müşterileri listeler.",
         {"tarih": DATE_SCHEMA, "musteri_adi": {**NAME_SCHEMA, "description": "Yalnızca bir müşteri için müşteri adı"},
          "en_fazla": LIMIT_SCHEMA},
         _receivables_aging),
    ToolSpec("geciken_alacaklar", "Vadesini en az min_gun gün geçmiş açık faturaları, tutarı büyükten küçüğe listeler.",
         {"min_gun": {"type": "integer", "minimum": 1, "maximum": 3650,
                      "description": "En az kaç gün gecikmiş olanlar"},
          "en_fazla": LIMIT_SCHEMA, "tarih": DATE_SCHEMA},
         _overdue_receivables, ("min_gun",)),
    ToolSpec("musteri_ozeti", "Bir müşterinin açık alacağını, son tahsilatlarını, bekleyen çeklerini ve son 12 aydaki "
                          "ödeme alışkanlığını özetler.",
         {"musteri_adi": {**NAME_SCHEMA, "description": "Müşteri adı (en az 3 harf)"}, "tarih": DATE_SCHEMA},
         _customer_summary, ("musteri_adi",)),
    ToolSpec("dso_hesapla", "Alacakların ortalama kaç günde tahsil edildiğini (DSO) seçilen dönem için hesaplar.",
         {"donem": {"type": "string", "enum": list(PERIODS), "description": "Hesaplama dönemi"}, "tarih": DATE_SCHEMA},
         _compute_dso, ("donem",)),
    ToolSpec("yaklasan_odemeler", "Önümüzdeki günlerde vadesi gelen ve vadesi geçmiş ödenmemiş tedarikçi faturalarını "
                              "listeler.",
         {"gun_sayisi": {"type": "integer", "minimum": 1, "maximum": 90,
                         "description": "Bugünden itibaren kaç günlük ödemeler"}, "en_fazla": LIMIT_SCHEMA},
         _upcoming_payments, ("gun_sayisi",)),
    ToolSpec("nakit_akis_tahmini", "Önümüzdeki haftaların bilinen nakit giriş ve çıkışlarını haftalık verir.",
         {"hafta_sayisi": {"type": "integer", "minimum": 1, "maximum": 13, "description": "Kaç haftalık (1-13)"}},
         _cash_flow_forecast, ("hafta_sayisi",)),
    ToolSpec("kur_riski", "Döviz cinsinden alacak ve banka bakiyelerinin açık pozisyonunu ve kur %10 değişirse TL "
                      "etkisini gösterir.",
         {"para_birimi": {"type": "string", "enum": ["EUR", "USD"], "description": "Yalnızca bir para birimi"},
          "tarih": DATE_SCHEMA},
         _fx_risk),
    ToolSpec("doviz_kuru", "TCMB döviz alış kurunu verir.",
         {"para_birimi": {"type": "string", "enum": ["USD", "EUR", "GBP"], "description": "Para birimi"},
          "tarih": DATE_SCHEMA},
         _fx_rate, ("para_birimi",)),
    ToolSpec("cek_portfoyu", "Müşterilerden alınan çekleri durumuna, vadesine ya da müşteriye göre listeler.",
         {"durum": {"type": "string", "enum": list(CHEQUE_STATUSES),
                    "description": "Çekin durumu; bekleyen = portföyde ya da tahsilde (varsayılan)"},
          "musteri_adi": {**NAME_SCHEMA, "description": "Yalnızca bu müşterinin çekleri"},
          "vade_gun": {"type": "integer", "minimum": 0, "maximum": 365,
                       "description": "Vadesine en fazla kaç gün kalanlar"},
          "en_fazla": LIMIT_SCHEMA, "tarih": DATE_SCHEMA},
         _cheque_portfolio),
    ToolSpec("pos_tahsilatlari", "Bir tarih aralığındaki POS satışlarını, iadeleri ve komisyonu özetler.",
         {"baslangic": {**DATE_SCHEMA, "description": "Aralığın ilk günü, YYYY-MM-DD"},
          "bitis": {**DATE_SCHEMA, "description": "Aralığın son günü, YYYY-MM-DD"}},
         _pos_collections, ("baslangic", "bitis")),
    ToolSpec("fatura_ara", "Satış ve iade faturalarını fatura numarasına, müşteriye ya da duruma göre arar.",
         {"fatura_no": {**ID_SCHEMA, "description": "Fatura numarası"},
          "musteri_adi": {**NAME_SCHEMA, "description": "Müşteri adı"},
          "durum": {"type": "string", "enum": ["acik", "kapali", "iade", "hepsi"], "description": "Faturanın durumu"},
          "en_fazla": LIMIT_SCHEMA, "tarih": DATE_SCHEMA},
         _search_invoices),
    ToolSpec("belge_ara", "Sözleşmelerde, şirket politikalarında ve ilgili kanun maddelerinde arar; en ilgili maddelerin "
                      "metnini parça kimliğiyle döndürür. Rolün göremediği belgeler aranmaz.",
         {"sorgu": {"type": "string", "minLength": 3, "maxLength": 300,
                    "description": "Ne aranıyor; ör. 'Bereket İmalat ödeme vadesi'"},
          "en_fazla": {"type": "integer", "minimum": 1, "maximum": DOCUMENT_MAX_LIMIT,
                       "description": f"En fazla kaç madde (varsayılan {DOCUMENT_DEFAULT_LIMIT})"}},
         _search_documents, ("sorgu",)),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {a.name: a for a in TOOLS}
AGGREGATE_TOOLS = frozenset({"dso_hesapla", "nakit_akis_tahmini", "kur_riski", "pos_tahsilatlari"})
