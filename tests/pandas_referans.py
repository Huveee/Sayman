"""Faz 2 metriklerinin pandas ile ikinci, bağımsız hesabı (yalnızca testler kullanır).

DuckDB tarafıyla ortak kod paylaşmaz: CSV'leri kendisi okur, tipleri kendisi kurar, yuvarlamayı kendisi
yapar. Tanımlar sayman/ambar/metrikler.py'nin belgesindeki tanımlardır; iki hesap aynı sonucu vermelidir.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

TABLOLAR = ("musteriler", "faturalar", "pos_islemleri", "tahsilatlar", "cekler", "cek_hareketleri", "tedarikciler",
            "alis_faturalari", "odemeler", "banka_hesaplari", "banka_hareketleri", "doviz_kurlari")
DILIMLER = (("vadesi_gelmemis", -10**9, 0), ("gecikme_1_30", 1, 30), ("gecikme_31_60", 31, 60),
            ("gecikme_61_90", 61, 90), ("gecikme_91_180", 91, 180), ("gecikme_180_ustu", 181, 10**9))


def _tur(sutun: str) -> str:
    if sutun.endswith("_kurus") or sutun in ("vade_gun", "kalem_sayisi"):
        return "int"
    if sutun == "yurtici":
        return "bool"
    if sutun in ("kur", "alis"):
        return "ondalik"
    if sutun == "tarih" or sutun.endswith("tarihi"):
        return "gun"
    return "metin"


def oku(klasor: Path) -> dict[str, pd.DataFrame]:
    T = {}
    for ad in TABLOLAR:
        df = pd.read_csv(Path(klasor) / f"{ad}.csv", dtype=str, keep_default_na=False, encoding="utf-8")
        for s in df.columns:
            bos = df[s] == ""
            tur = _tur(s)
            if tur == "int":
                df[s] = pd.array([None if b else int(v) for v, b in zip(df[s], bos)], dtype="Int64")
            elif tur == "bool":
                df[s] = df[s] == "1"
            elif tur == "ondalik":
                df[s] = [None if b else Decimal(v) for v, b in zip(df[s], bos)]
            elif tur == "gun":
                df[s] = pd.to_datetime(df[s].where(~bos, None), format="%Y-%m-%d")
            else:
                df[s] = df[s].where(~bos, None)
        T[ad] = df
    return T


def kur_carp(kurus, kur: Decimal) -> int:
    return int((Decimal(int(kurus)) * kur).to_integral_value(rounding=ROUND_HALF_EVEN))


def _ts(tarih: dt.date) -> pd.Timestamp:
    return pd.Timestamp(tarih)


def _gun(x) -> dt.date:
    return pd.Timestamp(x).date()


def _kurlar(T, d: pd.Timestamp) -> dict[str, Decimal]:
    k = T["doviz_kurlari"]
    k = k[k["tarih"] <= d].sort_values(["para_birimi", "tarih"])
    kurlar = {pb: g["alis"].iloc[-1] for pb, g in k.groupby("para_birimi")}
    kurlar["TRY"] = Decimal(1)
    return kurlar


def _kur(kurlar: dict, pb: str) -> Decimal:
    if pb not in kurlar:
        raise LookupError(f"kur bulunamadı: {pb}")
    return kurlar[pb]


def _acik(T, d: pd.Timestamp) -> pd.DataFrame:
    f = T["faturalar"]
    sat = f[(f["tur"] == "satis") & (f["fatura_tarihi"] <= d)].copy()
    t = T["tahsilatlar"]
    t = t[(t["tarih"] <= d) & t["fatura_id"].notna()]
    tah = {k: int(v) for k, v in t.groupby("fatura_id")["tutar_kurus"].sum().items()}
    i = f[(f["tur"] == "iade") & (f["fatura_tarihi"] <= d) & f["iliskili_fatura_id"].notna()]
    iad = {k: int(v) for k, v in i.assign(u=i["uygulanan_kurus"].fillna(0)).groupby("iliskili_fatura_id")["u"].sum().items()}
    sat["acik_kurus"] = [int(a) - tah.get(fid, 0) - iad.get(fid, 0)
                         for a, fid in zip(sat["tutar_kurus"], sat["fatura_id"])]
    sat["gecikme_gun"] = (d - sat["vade_tarihi"]).dt.days.astype(int)
    poz = sat[sat["acik_kurus"] > 0].copy()
    poz["acik_try_kurus"] = [kur_carp(a, k) for a, k in zip(poz["acik_kurus"], poz["kur"])]
    return poz


def _eslesmemis(T, d: pd.Timestamp) -> pd.DataFrame:
    t = T["tahsilatlar"]
    t = t[t["fatura_id"].isna() & (t["tarih"] <= d)].copy()
    if t.empty:
        return t.assign(try_kurus=pd.Series(dtype=object))
    kurlar = T["doviz_kurlari"].sort_values("tarih")[["tarih", "para_birimi", "alis"]]
    t = pd.merge_asof(t.sort_values("tarih"), kurlar, on="tarih", by="para_birimi", direction="backward")
    deger = []
    for pb, tutar, alis in zip(t["para_birimi"], t["tutar_kurus"], t["alis"]):
        if pb == "TRY":
            deger.append(int(tutar))
        elif alis is None or (isinstance(alis, float) and np.isnan(alis)):
            raise LookupError(f"kur bulunamadı: {pb}")
        else:
            deger.append(kur_carp(tutar, alis))
    return t.assign(try_kurus=deger)


def _bakiyeler(T, d: pd.Timestamp) -> pd.DataFrame:
    h = T["banka_hesaplari"]
    h = h[h["acilis_tarihi"] <= d].copy()
    hr = T["banka_hareketleri"]
    toplam = hr[hr["tarih"] <= d].groupby("hesap_id")["tutar_kurus"].sum()
    h["bakiye_kurus"] = [int(a) + int(toplam.get(i, 0)) for a, i in zip(h["acilis_bakiye_kurus"], h["hesap_id"])]
    return h


def _acik_borc(T, d: pd.Timestamp) -> pd.DataFrame:
    a = T["alis_faturalari"]
    a = a[a["tarih"] <= d].copy()
    o = T["odemeler"]
    od = o[(o["tarih"] <= d) & o["alis_fatura_id"].notna()].groupby("alis_fatura_id")["tutar_kurus"].sum()
    a["acik_kurus"] = [int(t) - int(od.get(i, 0)) for t, i in zip(a["tutar_kurus"], a["alis_fatura_id"])]
    return a


# ---------------------------------------------------------------- metrikler


def nakit_pozisyonu(T, tarih: dt.date) -> dict:
    d = _ts(tarih)
    kurlar = _kurlar(T, d)
    hesaplar = []
    for _, h in _bakiyeler(T, d).sort_values("hesap_id").iterrows():
        kur = _kur(kurlar, h["para_birimi"])
        hesaplar.append({"hesap_id": h["hesap_id"], "banka": h["banka"], "para_birimi": h["para_birimi"],
                         "bakiye_kurus": h["bakiye_kurus"], "kur": kur, "bakiye_try_kurus": kur_carp(h["bakiye_kurus"], kur)})
    return {"tarih": tarih, "hesaplar": hesaplar, "toplam_try_kurus": sum(h["bakiye_try_kurus"] for h in hesaplar)}


def yaslandirma(T, tarih: dt.date) -> dict:
    d = _ts(tarih)
    poz = _acik(T, d)
    esl = _eslesmemis(T, d)
    sutunlar = [f"{ad}_kurus" for ad, _, _ in DILIMLER]
    satir = {}
    for m, g, tutar in zip(poz["musteri_id"], poz["gecikme_gun"], poz["acik_try_kurus"]):
        kayit = satir.setdefault(m, dict.fromkeys(sutunlar + ["eslesmemis_tahsilat_kurus"], 0))
        dilim = next(ad for ad, alt, ust in DILIMLER if alt <= g <= ust)
        kayit[f"{dilim}_kurus"] += tutar
    for m, tutar in zip(esl["musteri_id"], esl["try_kurus"]):
        kayit = satir.setdefault(m, dict.fromkeys(sutunlar + ["eslesmemis_tahsilat_kurus"], 0))
        kayit["eslesmemis_tahsilat_kurus"] -= tutar
    ad = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["ad"]))
    musteriler = []
    for m, kayit in satir.items():
        musteriler.append({"musteri_id": m, "ad": ad.get(m), **kayit, "toplam_kurus": sum(kayit.values())})
    musteriler.sort(key=lambda r: (-r["toplam_kurus"], r["musteri_id"]))
    hepsi = sutunlar + ["eslesmemis_tahsilat_kurus", "toplam_kurus"]
    return {"tarih": tarih, "musteriler": musteriler, "toplam": {s: sum(r[s] for r in musteriler) for s in hepsi}}


def dso(T, tarih: dt.date, gun: int = 365) -> dict:
    alacak = yaslandirma(T, tarih)["toplam"]["toplam_kurus"]
    bas, bit = _ts(tarih - dt.timedelta(days=gun - 1)), _ts(tarih)
    f, p = T["faturalar"], T["pos_islemleri"]
    satis = int(f[(f["fatura_tarihi"] >= bas) & (f["fatura_tarihi"] <= bit)]["tutar_try_kurus"].sum()) + int(
        p[(p["islem_tarihi"] >= bas) & (p["islem_tarihi"] <= bit)]["tutar_kurus"].sum())
    deger = None
    if satis > 0:
        deger = (Decimal(gun) * Decimal(alacak) / Decimal(satis)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return {"tarih": tarih, "gun": gun, "alacak_kurus": alacak, "satis_kurus": satis, "dso_gun": deger}


def geciken_alacaklar(T, tarih: dt.date, en_az_gun: int = 1) -> dict:
    poz = _acik(T, _ts(tarih))
    poz = poz[poz["gecikme_gun"] >= en_az_gun]
    ad = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["ad"]))
    faturalar = [{"fatura_id": r["fatura_id"], "musteri_id": r["musteri_id"], "ad": ad.get(r["musteri_id"]),
                  "fatura_tarihi": _gun(r["fatura_tarihi"]), "vade_tarihi": _gun(r["vade_tarihi"]),
                  "gecikme_gun": int(r["gecikme_gun"]), "para_birimi": r["para_birimi"],
                  "acik_kurus": int(r["acik_kurus"]), "acik_try_kurus": int(r["acik_try_kurus"])}
                 for _, r in poz.iterrows()]
    faturalar.sort(key=lambda r: (-r["acik_try_kurus"], r["fatura_id"]))
    return {"tarih": tarih, "en_az_gun": en_az_gun, "faturalar": faturalar,
            "toplam_try_kurus": sum(r["acik_try_kurus"] for r in faturalar)}


def yaklasan_odemeler(T, tarih: dt.date, gun: int = 30) -> dict:
    d = _ts(tarih)
    a = _acik_borc(T, d)
    a = a[(a["acik_kurus"] > 0) & (a["vade_tarihi"] <= d + pd.Timedelta(days=gun))]
    ad = dict(zip(T["tedarikciler"]["tedarikci_id"], T["tedarikciler"]["ad"]))
    odemeler = [{"alis_fatura_id": r["alis_fatura_id"], "tedarikci_id": r["tedarikci_id"], "ad": ad.get(r["tedarikci_id"]),
                 "kategori": r["kategori"], "tarih": _gun(r["tarih"]), "vade_tarihi": _gun(r["vade_tarihi"]),
                 "kalan_gun": (r["vade_tarihi"] - d).days, "acik_kurus": int(r["acik_kurus"])}
                for _, r in a.iterrows()]
    odemeler.sort(key=lambda r: (r["vade_tarihi"], r["alis_fatura_id"]))
    return {"tarih": tarih, "gun": gun, "odemeler": odemeler, "toplam_kurus": sum(r["acik_kurus"] for r in odemeler)}


def kur_riski(T, tarih: dt.date) -> dict:
    d = _ts(tarih)
    kurlar = _kurlar(T, d)
    m, h = T["musteriler"], T["banka_hesaplari"]
    pbler = sorted((set(m[m["ilk_islem_tarihi"] <= d]["para_birimi"]) | set(h[h["acilis_tarihi"] <= d]["para_birimi"]))
                   - {"TRY"})
    poz = _acik(T, d)
    t = T["tahsilatlar"]
    esl = t[t["fatura_id"].isna() & (t["tarih"] <= d)]
    bak = _bakiyeler(T, d)
    dovizler = []
    for pb in pbler:
        kur = _kur(kurlar, pb)
        p = poz[poz["para_birimi"] == pb]
        alacak = int(p["acik_kurus"].sum()) - int(esl[esl["para_birimi"] == pb]["tutar_kurus"].sum())
        fark = sum(kur_carp(a, kur) - kur_carp(a, k) for a, k in zip(p["acik_kurus"], p["kur"]))
        banka = int(bak[bak["para_birimi"] == pb]["bakiye_kurus"].sum())
        toplam = alacak + banka
        dovizler.append({
            "para_birimi": pb, "alacak_doviz_kurus": alacak, "banka_doviz_kurus": banka, "toplam_doviz_kurus": toplam,
            "kur": kur, "toplam_try_kurus": kur_carp(toplam, kur),
            "yuzde_10_etki_kurus": int((Decimal(toplam) * kur * Decimal("0.1")).to_integral_value(rounding=ROUND_HALF_EVEN)),
            "alacak_kur_farki_kurus": fark,
        })
    return {"tarih": tarih, "dovizler": dovizler, "toplam_try_kurus": sum(x["toplam_try_kurus"] for x in dovizler)}


def _ortalama_gecikmeler(T, d: pd.Timestamp) -> tuple[dict[str, int], int]:
    f = T["faturalar"]
    t = T["tahsilatlar"]
    t = t[t["fatura_id"].notna() & (t["tarih"] <= d)][["fatura_id", "tarih", "tutar_kurus"]]
    i = f[(f["tur"] == "iade") & f["iliskili_fatura_id"].notna() & (f["fatura_tarihi"] <= d)]
    i = pd.DataFrame({"fatura_id": i["iliskili_fatura_id"], "tarih": i["fatura_tarihi"],
                      "tutar_kurus": i["uygulanan_kurus"].fillna(0)})
    olgu = pd.concat([t, i]).groupby(["fatura_id", "tarih"], as_index=False)["tutar_kurus"].sum()
    olgu = olgu.sort_values(["fatura_id", "tarih"])
    olgu["kumulatif"] = olgu.groupby("fatura_id")["tutar_kurus"].cumsum()
    sat = f[(f["tur"] == "satis") & (f["fatura_tarihi"] <= d)]
    tutar = dict(zip(sat["fatura_id"], sat["tutar_kurus"]))
    ilk = {}
    for fid, gun, kum in zip(olgu["fatura_id"], olgu["tarih"], olgu["kumulatif"]):
        if fid in tutar and fid not in ilk and kum >= tutar[fid]:
            ilk[fid] = gun
    toplam, adet = {}, {}
    alt = d - pd.Timedelta(days=364)
    for fid, m, vade, ft, tt in zip(sat["fatura_id"], sat["musteri_id"], sat["vade_tarihi"], sat["fatura_tarihi"],
                                   sat["tutar_kurus"]):
        kapanis = ft if tt <= 0 else ilk.get(fid)
        if kapanis is None or not (alt <= kapanis <= d):
            continue
        g = max(0, (kapanis - vade).days)
        toplam[m] = toplam.get(m, 0) + g
        adet[m] = adet.get(m, 0) + 1
    sirket = sum(toplam.values()) // sum(adet.values()) if adet else 0
    return {m: toplam[m] // adet[m] for m in toplam}, sirket


def nakit_akis_tahmini(T, tarih: dt.date, hafta: int = 13) -> dict:
    d = _ts(tarih)
    kurlar = _kurlar(T, d)
    yarin = d + pd.Timedelta(days=1)
    pb = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["para_birimi"]))

    ch = T["cek_hareketleri"]
    ch = ch[ch["tarih"] <= d].sort_values(["cek_id", "tarih", "cek_hareket_id"])
    son = ch.groupby("cek_id")["durum"].last()
    c = T["cekler"]
    c = c[(c["alis_tarihi"] <= d) & c["cek_id"].map(son).isin(["portfoyde", "tahsilde"])]
    t = T["tahsilatlar"]
    esl = t[t["fatura_id"].isna() & (t["tarih"] <= d)]
    havuz = pd.concat([c[["musteri_id", "tutar_kurus"]], esl[["musteri_id", "tutar_kurus"]]]
                      ).groupby("musteri_id")["tutar_kurus"].sum()

    poz = _acik(T, d).sort_values(["musteri_id", "vade_tarihi", "fatura_id"])
    poz["onceki"] = poz.groupby("musteri_id")["acik_kurus"].cumsum() - poz["acik_kurus"]
    ort, sirket = _ortalama_gecikmeler(T, d)

    kalemler = []  # (tür, gün ya da None, tutar)
    for m, a, onceki, g, vade, p in zip(poz["musteri_id"], poz["acik_kurus"], poz["onceki"], poz["gecikme_gun"],
                                        poz["vade_tarihi"], poz["para_birimi"]):
        kalan = int(a) - min(int(a), max(0, int(havuz.get(m, 0)) - int(onceki)))
        if kalan <= 0:
            continue
        gun = None if g > 90 else max(vade + pd.Timedelta(days=ort.get(m, sirket)), yarin)
        kalemler.append(("alacak", gun, kur_carp(kalan, _kur(kurlar, p))))
    for m, vade, tutar in zip(c["musteri_id"], c["vade_tarihi"], c["tutar_kurus"]):
        kalemler.append(("cek", max(vade, yarin), kur_carp(tutar, _kur(kurlar, pb[m]))))
    p = T["pos_islemleri"]
    p = p[(p["islem_tarihi"] <= d) & (p["valor_tarihi"] > d)]
    for valor, tutar, kom in zip(p["valor_tarihi"], p["tutar_kurus"], p["komisyon_kurus"]):
        kalemler.append(("pos", valor, int(tutar) - int(kom)))
    b = _acik_borc(T, d)
    b = b[b["acik_kurus"] > 0]
    for vade, tutar in zip(b["vade_tarihi"], b["acik_kurus"]):
        kalemler.append(("tedarikci", max(vade, yarin), int(tutar)))

    kova: dict[tuple, int] = {}
    for tur, gun, tutar in kalemler:
        if gun is None:
            k = -1
        else:
            k = ((gun - d).days - 1) // 7 + 1
            k = 0 if k > hafta else k
        kova[(tur, k)] = kova.get((tur, k), 0) + tutar

    acilis = nakit_pozisyonu(T, tarih)["toplam_try_kurus"]
    haftalar, bakiye = [], acilis
    for k in range(1, hafta + 1):
        giris = {tur: kova.get((tur, k), 0) for tur in ("alacak", "cek", "pos")}
        cikis = kova.get(("tedarikci", k), 0)
        net = sum(giris.values()) - cikis
        bakiye += net
        haftalar.append({"hafta": k, "bas": tarih + dt.timedelta(days=7 * (k - 1) + 1), "bit": tarih + dt.timedelta(days=7 * k),
                         "alacak_girisi_kurus": giris["alacak"], "cek_girisi_kurus": giris["cek"],
                         "pos_girisi_kurus": giris["pos"], "tedarikci_cikisi_kurus": cikis, "net_kurus": net,
                         "bilinen_kalemlerle_bakiye_kurus": bakiye})
    return {"tarih": tarih, "hafta_sayisi": hafta, "acilis_kurus": acilis, "haftalar": haftalar,
            "ufuk_disi_giris_kurus": sum(kova.get((tur, 0), 0) for tur in ("alacak", "cek", "pos")),
            "ufuk_disi_cikis_kurus": kova.get(("tedarikci", 0), 0),
            "supheli_alacak_kurus": kova.get(("alacak", -1), 0)}


METRIKLER = {
    "nakit_pozisyonu": nakit_pozisyonu,
    "yaslandirma": yaslandirma,
    "dso": dso,
    "geciken_alacaklar": geciken_alacaklar,
    "yaklasan_odemeler": yaklasan_odemeler,
    "kur_riski": kur_riski,
    "nakit_akis_tahmini": nakit_akis_tahmini,
}
