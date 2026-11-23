"""Invariants of the generated data. Each check returns a list of errors; an empty list means it passed.

The generation script writes the results to the report; the acceptance test reruns the same checks on the CSVs on disk.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sayman.data.table_io import SCHEMA
from sayman.data.as_of import DATE_COLUMN, cheque_statuses, as_of_view

MAX_SHOWN_ERRORS = 5  # at most this many sample errors are written per check


def _truncate(errors: list[str]) -> list[str]:
    return errors[:MAX_SHOWN_ERRORS] + ([f"... ve {len(errors) - MAX_SHOWN_ERRORS} hata daha"] if len(errors) > MAX_SHOWN_ERRORS else [])


def check_money_integer(tables) -> list[str]:
    h = []
    for name, rows in tables.items():
        for s in rows:
            for column, value in s.items():
                if isinstance(value, float) or (column.endswith("_kurus") and value is not None and type(value) is not int):
                    h.append(f"{name}.{column}: {value!r}")
    return _truncate(h)


def check_no_hidden_fields(tables) -> list[str]:
    h = [f"{name}: {sorted(set(s) ^ set(SCHEMA[name]))}" for name, rows in tables.items() for s in rows if set(s) != set(SCHEMA[name])]
    return _truncate(h)


def check_collection_links(tables, answer_key) -> list[str]:
    sales = {f["fatura_id"]: f for f in tables["faturalar"] if f["tur"] == "satis"}
    actual = answer_key["eslesmeyen_tahsilatlar"]
    h = []
    for t in tables["tahsilatlar"]:
        if t["eslesme"] == "eslesti":
            f = sales.get(t["fatura_id"])
        elif t["eslesme"] == "bilerek_eslesmemis" and t["fatura_id"] is None:
            f = sales.get(actual.get(t["tahsilat_id"]))
        else:
            f = None
        if f is None or t["musteri_id"] != f["musteri_id"] or t["para_birimi"] != f["para_birimi"]:
            h.append(f"{t['tahsilat_id']}: faturaya bağlı değil ya da müşteri/para birimi tutmuyor")
        elif not (f["fatura_tarihi"] <= t["tarih"]) or t["tutar_kurus"] <= 0:
            h.append(f"{t['tahsilat_id']}: tarih ya da tutar geçersiz")
    return _truncate(h)


def check_no_over_collection(tables) -> list[str]:
    paid = defaultdict(int)
    for t in tables["tahsilatlar"]:
        if t["fatura_id"]:
            paid[t["fatura_id"]] += t["tutar_kurus"]
    for f in tables["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"]:
            paid[f["iliskili_fatura_id"]] += f["uygulanan_kurus"] or 0
    amount = {f["fatura_id"]: f["tutar_kurus"] for f in tables["faturalar"]}
    return _truncate([f"{invoice_id}: {o} > {amount.get(invoice_id)}" for invoice_id, o in paid.items() if invoice_id not in amount or o > amount[invoice_id]])


def check_return_invoices(tables, end: dt.date) -> list[str]:
    sales = {f["fatura_id"]: f for f in tables["faturalar"] if f["tur"] == "satis"}
    refunded = defaultdict(int)
    for o in tables["odemeler"]:
        if o["tur"] == "musteri_iadesi":
            refunded[o["iade_fatura_id"]] += o["tutar_kurus"]
    h = []
    for f in tables["faturalar"]:
        if f["tur"] != "iade":
            continue
        amount = -f["tutar_kurus"]
        if amount <= 0 or not (0 <= (f["uygulanan_kurus"] or 0) <= amount):
            h.append(f"{f['fatura_id']}: iade tutarı ya da uygulanan tutar geçersiz")
            continue
        if f["iliskili_fatura_id"]:
            s = sales.get(f["iliskili_fatura_id"])
            if s is None or s["musteri_id"] != f["musteri_id"] or s["fatura_tarihi"] > f["fatura_tarihi"]:
                h.append(f"{f['fatura_id']}: bağlı satış faturası tutarsız")
        elif f["iade_eslesme"] != "yok" or f["uygulanan_kurus"]:
            h.append(f"{f['fatura_id']}: bağsız iade yanlış işaretli")
        if refunded[f["fatura_id"]] and f["uygulanan_kurus"] + refunded[f["fatura_id"]] != amount:
            h.append(f"{f['fatura_id']}: uygulanan + geri ödenen iade tutarına eşit değil")
    return _truncate(h)


def check_cheques(tables, end: dt.date) -> list[str]:
    collected = defaultdict(list)
    for t in tables["tahsilatlar"]:
        if t["cek_id"]:
            collected[t["cek_id"]].append(t)
    movements = defaultdict(list)
    for x in tables["cek_hareketleri"]:
        movements[x["cek_id"]].append(x)
    status = cheque_statuses(tables, end)
    h = []
    for c in tables["cekler"]:
        ordered = sorted(movements[c["cek_id"]], key=lambda x: (x["tarih"], x["cek_hareket_id"]))
        if not ordered or ordered[0]["durum"] != "portfoyde" or ordered[0]["tarih"] != c["alis_tarihi"]:
            h.append(f"{c['cek_id']}: ilk hareket portföye alış değil")
        if status.get(c["cek_id"]) == "tahsil_edildi":
            ts = collected[c["cek_id"]]
            if len(ts) != 1 or ts[0]["tutar_kurus"] != c["tutar_kurus"]:
                h.append(f"{c['cek_id']}: tahsil edilen çekin tek ve eşit tutarlı tahsilatı yok")
        elif collected[c["cek_id"]]:
            h.append(f"{c['cek_id']}: tahsil edilmemiş çekin tahsilatı var")
    return _truncate(h)


def check_bank_balances(tables) -> list[str]:
    balance = {x["hesap_id"]: x["acilis_bakiye_kurus"] for x in tables["banka_hesaplari"]}
    h = []
    for s in sorted(tables["banka_hareketleri"], key=lambda s: s["hareket_id"]):
        balance[s["hesap_id"]] += s["tutar_kurus"]
        if s["bakiye_kurus"] != balance[s["hesap_id"]]:
            h.append(f"{s['hareket_id']}: bakiye açılış + hareketlere eşit değil")
        if s["bakiye_kurus"] < 0:
            h.append(f"{s['hareket_id']}: {s['hesap_id']} eksi bakiye")
    return _truncate(h)


def check_bank_sources(tables) -> list[str]:
    source = Counter((s["kaynak_tur"], s["kaynak_id"]) for s in tables["banka_hareketleri"])
    h = [f"{t['tahsilat_id']}: bankada {source[('tahsilat', t['tahsilat_id'])]} kez"
         for t in tables["tahsilatlar"] if source[("tahsilat", t["tahsilat_id"])] != 1]
    h += [f"{o['odeme_id']}: bankada {source[('odeme', o['odeme_id'])]} kez"
          for o in tables["odemeler"] if source[("odeme", o["odeme_id"])] != 1]
    if sum(1 for k in source if k[0] == "tahsilat") != len(tables["tahsilatlar"]):
        h.append("bankada kaynağı olmayan tahsilat hareketi var")
    if sum(1 for k in source if k[0] == "odeme") != len(tables["odemeler"]):
        h.append("bankada kaynağı olmayan ödeme hareketi var")
    return _truncate(h)


def check_event_counts(answer_key, event_counts, now: dt.date) -> list[str]:
    h = []
    actual = Counter(o["tur"] for o in answer_key["olaylar"])
    if actual != Counter(dict(event_counts)):
        h.append(f"olay sayıları ayarla tutmuyor: {dict(actual)}")
    if sum(1 for o in answer_key["olaylar"] if dt.date.fromisoformat(o["tarih"]) > now) < 3:
        h.append("şimdiden sonra en az 3 olay yok")
    return h


def check_view_hides_future(tables, now: dt.date) -> list[str]:
    g = as_of_view(tables, now)
    return [f"{name}: şimdiden sonraki satır görünümde" for name, column in DATE_COLUMN.items()
            if column and any(s[column] > now for s in g[name])]


def run_all(tables, answer_key, event_counts, now: dt.date, end: dt.date) -> dict[str, list[str]]:
    return {
        "para_tamsayi_kurus": check_money_integer(tables),
        "sema_disi_alan_yok": check_no_hidden_fields(tables),
        "tahsilat_faturaya_bagli_ya_da_bilerek_eslesmemis": check_collection_links(tables, answer_key),
        "fazla_tahsilat_yok": check_no_over_collection(tables),
        "iade_faturalari_dogru": check_return_invoices(tables, end),
        "cekler_tutarli": check_cheques(tables, end),
        "banka_bakiye_acilis_arti_hareket": check_bank_balances(tables),
        "her_tahsilat_ve_odeme_bankada_bir_kez": check_bank_sources(tables),
        "olay_sayisi_ayardaki_kadar": check_event_counts(answer_key, event_counts, now),
        "gorunum_gelecegi_gizler": check_view_hides_future(tables, now),
    }
