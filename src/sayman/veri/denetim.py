"""Üretilen verinin değişmezleri. Her denetim hata listesi döner; boş liste geçti demektir.

Üretim betiği sonuçları rapora yazar; kabul testi aynı denetimleri diskteki CSV'lerden yeniden çalıştırır.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sayman.veri.cikti import SEMA
from sayman.veri.gorunum import TARIH_SUTUNU, cek_durumlari, gorunum

GOSTERILECEK = 5  # her denetimde en çok kaç örnek hata yazılır


def _kes(hatalar: list[str]) -> list[str]:
    return hatalar[:GOSTERILECEK] + ([f"... ve {len(hatalar) - GOSTERILECEK} hata daha"] if len(hatalar) > GOSTERILECEK else [])


def para_tamsayi(T) -> list[str]:
    h = []
    for ad, satirlar in T.items():
        for s in satirlar:
            for sutun, deger in s.items():
                if isinstance(deger, float) or (sutun.endswith("_kurus") and deger is not None and type(deger) is not int):
                    h.append(f"{ad}.{sutun}: {deger!r}")
    return _kes(h)


def gizli_alan_yok(T) -> list[str]:
    h = [f"{ad}: {sorted(set(s) ^ set(SEMA[ad]))}" for ad, satirlar in T.items() for s in satirlar if set(s) != set(SEMA[ad])]
    return _kes(h)


def tahsilat_baglantilari(T, anahtar) -> list[str]:
    satis = {f["fatura_id"]: f for f in T["faturalar"] if f["tur"] == "satis"}
    gercek = anahtar["eslesmeyen_tahsilatlar"]
    h = []
    for t in T["tahsilatlar"]:
        if t["eslesme"] == "eslesti":
            f = satis.get(t["fatura_id"])
        elif t["eslesme"] == "bilerek_eslesmemis" and t["fatura_id"] is None:
            f = satis.get(gercek.get(t["tahsilat_id"]))
        else:
            f = None
        if f is None or t["musteri_id"] != f["musteri_id"] or t["para_birimi"] != f["para_birimi"]:
            h.append(f"{t['tahsilat_id']}: faturaya bağlı değil ya da müşteri/para birimi tutmuyor")
        elif not (f["fatura_tarihi"] <= t["tarih"]) or t["tutar_kurus"] <= 0:
            h.append(f"{t['tahsilat_id']}: tarih ya da tutar geçersiz")
    return _kes(h)


def fazla_tahsilat_yok(T) -> list[str]:
    odenen = defaultdict(int)
    for t in T["tahsilatlar"]:
        if t["fatura_id"]:
            odenen[t["fatura_id"]] += t["tutar_kurus"]
    for f in T["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"]:
            odenen[f["iliskili_fatura_id"]] += f["uygulanan_kurus"] or 0
    tutar = {f["fatura_id"]: f["tutar_kurus"] for f in T["faturalar"]}
    return _kes([f"{fid}: {o} > {tutar.get(fid)}" for fid, o in odenen.items() if fid not in tutar or o > tutar[fid]])


def iade_faturalari(T, bitis: dt.date) -> list[str]:
    satis = {f["fatura_id"]: f for f in T["faturalar"] if f["tur"] == "satis"}
    geri = defaultdict(int)
    for o in T["odemeler"]:
        if o["tur"] == "musteri_iadesi":
            geri[o["iade_fatura_id"]] += o["tutar_kurus"]
    h = []
    for f in T["faturalar"]:
        if f["tur"] != "iade":
            continue
        tutar = -f["tutar_kurus"]
        if tutar <= 0 or not (0 <= (f["uygulanan_kurus"] or 0) <= tutar):
            h.append(f"{f['fatura_id']}: iade tutarı ya da uygulanan tutar geçersiz")
            continue
        if f["iliskili_fatura_id"]:
            s = satis.get(f["iliskili_fatura_id"])
            if s is None or s["musteri_id"] != f["musteri_id"] or s["fatura_tarihi"] > f["fatura_tarihi"]:
                h.append(f"{f['fatura_id']}: bağlı satış faturası tutarsız")
        elif f["iade_eslesme"] != "yok" or f["uygulanan_kurus"]:
            h.append(f"{f['fatura_id']}: bağsız iade yanlış işaretli")
        if geri[f["fatura_id"]] and f["uygulanan_kurus"] + geri[f["fatura_id"]] != tutar:
            h.append(f"{f['fatura_id']}: uygulanan + geri ödenen iade tutarına eşit değil")
    return _kes(h)


def cekler(T, bitis: dt.date) -> list[str]:
    tahsilat = defaultdict(list)
    for t in T["tahsilatlar"]:
        if t["cek_id"]:
            tahsilat[t["cek_id"]].append(t)
    hareket = defaultdict(list)
    for x in T["cek_hareketleri"]:
        hareket[x["cek_id"]].append(x)
    durum = cek_durumlari(T, bitis)
    h = []
    for c in T["cekler"]:
        sirali = sorted(hareket[c["cek_id"]], key=lambda x: (x["tarih"], x["cek_hareket_id"]))
        if not sirali or sirali[0]["durum"] != "portfoyde" or sirali[0]["tarih"] != c["alis_tarihi"]:
            h.append(f"{c['cek_id']}: ilk hareket portföye alış değil")
        if durum.get(c["cek_id"]) == "tahsil_edildi":
            ts = tahsilat[c["cek_id"]]
            if len(ts) != 1 or ts[0]["tutar_kurus"] != c["tutar_kurus"]:
                h.append(f"{c['cek_id']}: tahsil edilen çekin tek ve eşit tutarlı tahsilatı yok")
        elif tahsilat[c["cek_id"]]:
            h.append(f"{c['cek_id']}: tahsil edilmemiş çekin tahsilatı var")
    return _kes(h)


def banka_bakiyeleri(T) -> list[str]:
    bakiye = {x["hesap_id"]: x["acilis_bakiye_kurus"] for x in T["banka_hesaplari"]}
    h = []
    for s in sorted(T["banka_hareketleri"], key=lambda s: s["hareket_id"]):
        bakiye[s["hesap_id"]] += s["tutar_kurus"]
        if s["bakiye_kurus"] != bakiye[s["hesap_id"]]:
            h.append(f"{s['hareket_id']}: bakiye açılış + hareketlere eşit değil")
        if s["bakiye_kurus"] < 0:
            h.append(f"{s['hareket_id']}: {s['hesap_id']} eksi bakiye")
    return _kes(h)


def banka_kaynaklari(T) -> list[str]:
    kaynak = Counter((s["kaynak_tur"], s["kaynak_id"]) for s in T["banka_hareketleri"])
    h = [f"{t['tahsilat_id']}: bankada {kaynak[('tahsilat', t['tahsilat_id'])]} kez"
         for t in T["tahsilatlar"] if kaynak[("tahsilat", t["tahsilat_id"])] != 1]
    h += [f"{o['odeme_id']}: bankada {kaynak[('odeme', o['odeme_id'])]} kez"
          for o in T["odemeler"] if kaynak[("odeme", o["odeme_id"])] != 1]
    if sum(1 for k in kaynak if k[0] == "tahsilat") != len(T["tahsilatlar"]):
        h.append("bankada kaynağı olmayan tahsilat hareketi var")
    if sum(1 for k in kaynak if k[0] == "odeme") != len(T["odemeler"]):
        h.append("bankada kaynağı olmayan ödeme hareketi var")
    return _kes(h)


def olay_sayisi(anahtar, olay_sayilari, simdi: dt.date) -> list[str]:
    h = []
    gercek = Counter(o["tur"] for o in anahtar["olaylar"])
    if gercek != Counter(dict(olay_sayilari)):
        h.append(f"olay sayıları ayarla tutmuyor: {dict(gercek)}")
    if sum(1 for o in anahtar["olaylar"] if dt.date.fromisoformat(o["tarih"]) > simdi) < 3:
        h.append("şimdiden sonra en az 3 olay yok")
    return h


def gorunum_gelecegi_gizler(T, simdi: dt.date) -> list[str]:
    g = gorunum(T, simdi)
    return [f"{ad}: şimdiden sonraki satır görünümde" for ad, sutun in TARIH_SUTUNU.items()
            if sutun and any(s[sutun] > simdi for s in g[ad])]


def hepsi(T, anahtar, olay_sayilari, simdi: dt.date, bitis: dt.date) -> dict[str, list[str]]:
    return {
        "para_tamsayi_kurus": para_tamsayi(T),
        "sema_disi_alan_yok": gizli_alan_yok(T),
        "tahsilat_faturaya_bagli_ya_da_bilerek_eslesmemis": tahsilat_baglantilari(T, anahtar),
        "fazla_tahsilat_yok": fazla_tahsilat_yok(T),
        "iade_faturalari_dogru": iade_faturalari(T, bitis),
        "cekler_tutarli": cekler(T, bitis),
        "banka_bakiye_acilis_arti_hareket": banka_bakiyeleri(T),
        "her_tahsilat_ve_odeme_bankada_bir_kez": banka_kaynaklari(T),
        "olay_sayisi_ayardaki_kadar": olay_sayisi(anahtar, olay_sayilari, simdi),
        "gorunum_gelecegi_gizler": gorunum_gelecegi_gizler(T, simdi),
    }
