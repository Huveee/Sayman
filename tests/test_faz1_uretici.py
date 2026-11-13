"""Faz 1: sentetik şirket üretici, olaylar, as-of görünüm ve kalibrasyon ölçümleri (örnek veriyle)."""

import datetime as dt
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from sayman.veri import denetim, kalibrasyon
from sayman.veri.cikti import SEMA, csv_oku, csv_yaz, tablo_hash, veri_hash
from sayman.veri.gorunum import TARIH_SUTUNU, acik_tutarlar, banka_bakiyeleri, cek_durumlari, gorunum
from sayman.veri.ornek import ornek_ham_satirlar, ornek_kurlar
from sayman.veri.uci import temizle
from sayman.veri.uretici import Ayarlar, is_gunune_al, uret

KOK = Path(__file__).resolve().parents[1]
AYAR = Ayarlar()


@pytest.fixture(scope="module")
def kalemler():
    return temizle(ornek_ham_satirlar()).kalemler


@pytest.fixture(scope="module")
def kurlar():
    return ornek_kurlar()


# Üretici testleri üç tohumla çalışır: olayların yerleşmesi ve hikâyeleri tek bir tohuma bağlı kalmasın.
@pytest.fixture(scope="module", params=[42, 43, 44], ids=lambda t: f"tohum{t}")
def tohum(request):
    return request.param


@pytest.fixture(scope="module")
def uretim(kalemler, kurlar, tohum):
    return uret(kalemler, kurlar, replace(AYAR, tohum=tohum))


@pytest.fixture(scope="module")
def temel(kalemler, kurlar, tohum):
    return uret(kalemler, kurlar, replace(AYAR, tohum=tohum, olaylar_acik=False))


@pytest.fixture(scope="module")
def T(uretim):
    return uretim.tablolar


# ---------------------------------------------------------------- tekrarlanabilirlik

HASH_KODU = (
    "from sayman.veri.ornek import ornek_ham_satirlar, ornek_kurlar\n"
    "from sayman.veri.uci import temizle\n"
    "from sayman.veri.uretici import uret\n"
    "from sayman.veri.cikti import veri_hash\n"
    "print(veri_hash(uret(temizle(ornek_ham_satirlar()).kalemler, ornek_kurlar()).tablolar)[0])\n"
)


def _ayri_surecte_hash(hash_tohumu: str) -> str:
    ortam = {**os.environ, "PYTHONHASHSEED": hash_tohumu, "PYTHONPATH": str(KOK / "src")}
    cikti = subprocess.run([sys.executable, "-c", HASH_KODU], env=ortam, capture_output=True, text=True, check=True)
    return cikti.stdout.strip()


def test_ayni_tohum_iki_ayri_surecte_ayni_hash(kalemler, kurlar):
    birinci, ikinci = _ayri_surecte_hash("1"), _ayri_surecte_hash("12345")
    assert birinci == ikinci == veri_hash(uret(kalemler, kurlar, AYAR).tablolar)[0]


def test_farkli_tohum_farkli_veri(kalemler, kurlar, uretim, tohum):
    baska = uret(kalemler, kurlar, replace(AYAR, tohum=tohum + 100))
    assert veri_hash(baska.tablolar)[0] != veri_hash(uretim.tablolar)[0]


def test_csv_gidis_donus_hashi_korur(T, tmp_path):
    csv_yaz(T, tmp_path)
    assert veri_hash(csv_oku(tmp_path)) == veri_hash(T)


# ---------------------------------------------------------------- yapı ve para


def test_tablolar_semaya_uyar_ve_kimlikler_tekil(T):
    assert set(T) == set(SEMA)
    for ad, satirlar in T.items():
        for s in satirlar:
            assert set(s) == set(SEMA[ad]), ad
        kimlikler = [s[SEMA[ad][0]] for s in satirlar]
        assert len(kimlikler) == len(set(kimlikler)), ad


def test_para_tamsayi_kurus_ve_hic_float_yok(T):
    for ad, satirlar in T.items():
        for s in satirlar:
            for sutun, deger in s.items():
                assert not isinstance(deger, float), (ad, sutun)
                if sutun.endswith("_kurus") and deger is not None:
                    assert type(deger) is int, (ad, sutun)


def test_cevap_anahtari_tablolarda_yok(uretim, T):
    for satirlar in T.values():
        for s in satirlar:
            assert not any(k.startswith("_") for k in s)
    sutunlar = {s for v in SEMA.values() for s in v}
    assert not sutunlar & {"profil", "olay", "olay_id", "gercek_fatura", "batik"}
    eslesmeyen = [t for t in T["tahsilatlar"] if t["eslesme"] == "bilerek_eslesmemis"]
    assert eslesmeyen and all(t["fatura_id"] is None for t in eslesmeyen)


def test_musteri_adlari_tekil_ve_uci_aciklamasi_tasinmaz(T):
    adlar = [m["ad"] for m in T["musteriler"]]
    assert len(adlar) == len(set(adlar))
    assert "Description" not in {s for v in SEMA.values() for s in v}


def test_tarihler_15_yil_kaydirilmis_ay_ve_haftanin_gunu_korunmus(uretim, T):
    # 15 yıl 5479 gün; en yakın tam hafta 5481: tarihler en çok iki gün kayar, ay ve haftanın günü yerinde kalır.
    assert uretim.kaydirma_gun == 5481 and uretim.kaydirma_gun % 7 == 0
    ilk = dt.date(2009, 12, 1) + dt.timedelta(days=uretim.kaydirma_gun)
    assert ilk == uretim.veri_baslangic == dt.date(2024, 12, 3) and ilk.weekday() == dt.date(2009, 12, 1).weekday()
    gunler = [f["fatura_tarihi"] for f in T["faturalar"]]
    assert min(gunler) == uretim.veri_baslangic and max(gunler) <= AYAR.veri_bitis
    # Veri bitişinden sonraya düşen son haftalar (UCI'de 10 Kasım 2011 sonrası) kullanılmaz.
    assert uretim.bitisten_sonra_kalan_kalem > 0
    assert dt.date(2011, 11, 10) + dt.timedelta(days=uretim.kaydirma_gun) == AYAR.veri_bitis


# ---------------------------------------------------------------- bağlantılar


def test_her_tahsilat_bir_faturaya_bagli_ya_da_bilerek_eslesmemis(uretim, T):
    satis = {f["fatura_id"]: f for f in T["faturalar"] if f["tur"] == "satis"}
    anahtar = uretim.anahtar["eslesmeyen_tahsilatlar"]
    for t in T["tahsilatlar"]:
        if t["eslesme"] == "eslesti":
            f = satis[t["fatura_id"]]
        else:
            assert t["eslesme"] == "bilerek_eslesmemis" and t["fatura_id"] is None
            f = satis[anahtar[t["tahsilat_id"]]]
        assert t["musteri_id"] == f["musteri_id"] and t["para_birimi"] == f["para_birimi"]
        assert f["fatura_tarihi"] <= t["tarih"] <= AYAR.veri_bitis
        assert t["tutar_kurus"] > 0
    assert set(anahtar) == {t["tahsilat_id"] for t in T["tahsilatlar"] if t["eslesme"] != "eslesti"}


def test_hicbir_fatura_fazla_tahsil_edilmez(T):
    odenen = defaultdict(int)
    for t in T["tahsilatlar"]:
        if t["fatura_id"]:
            odenen[t["fatura_id"]] += t["tutar_kurus"]
    for f in T["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"]:
            odenen[f["iliskili_fatura_id"]] += f["uygulanan_kurus"]
    tutar = {f["fatura_id"]: f["tutar_kurus"] for f in T["faturalar"]}
    for fid, toplam in odenen.items():
        assert toplam <= tutar[fid], fid


def test_iade_faturalari_dogru_islenir(T):
    satis = {f["fatura_id"]: f for f in T["faturalar"] if f["tur"] == "satis"}
    geri_odeme = defaultdict(int)
    for o in T["odemeler"]:
        if o["tur"] == "musteri_iadesi":
            geri_odeme[o["iade_fatura_id"]] += o["tutar_kurus"]
    iadeler = [f for f in T["faturalar"] if f["tur"] == "iade"]
    assert iadeler and any(f["iade_eslesme"] == "yok" for f in iadeler)
    for f in iadeler:
        assert f["tutar_kurus"] < 0 and f["vade_tarihi"] == f["fatura_tarihi"]
        assert 0 <= f["uygulanan_kurus"] <= -f["tutar_kurus"]
        if f["iliskili_fatura_id"]:
            s = satis[f["iliskili_fatura_id"]]
            assert s["musteri_id"] == f["musteri_id"] and s["fatura_tarihi"] <= f["fatura_tarihi"]
            assert f["iade_eslesme"] in ("stok_kodu", "son_fatura")
        else:
            assert f["iade_eslesme"] == "yok" and f["uygulanan_kurus"] == 0
        if is_gunune_al(f["fatura_tarihi"] + dt.timedelta(days=5)) <= AYAR.veri_bitis:
            assert f["uygulanan_kurus"] + geri_odeme[f["fatura_id"]] == -f["tutar_kurus"], f["fatura_id"]


def test_cekler_ve_hareketleri_tutarli(uretim, T):
    tahsilat = defaultdict(list)
    for t in T["tahsilatlar"]:
        if t["cek_id"]:
            tahsilat[t["cek_id"]].append(t)
    hareketler = defaultdict(list)
    for h in T["cek_hareketleri"]:
        hareketler[h["cek_id"]].append(h)
    durumlar = cek_durumlari(T, AYAR.veri_bitis)
    assert Counter(durumlar.values())["karsiliksiz"] >= 3
    for c in T["cekler"]:
        sirali = sorted(hareketler[c["cek_id"]], key=lambda h: (h["tarih"], h["cek_hareket_id"]))
        assert sirali[0]["durum"] == "portfoyde" and sirali[0]["tarih"] == c["alis_tarihi"]
        assert c["alis_tarihi"] <= c["vade_tarihi"] and all(h["tarih"] <= AYAR.veri_bitis for h in sirali)
        if durumlar[c["cek_id"]] == "tahsil_edildi":
            assert len(tahsilat[c["cek_id"]]) == 1
            t = tahsilat[c["cek_id"]][0]
            assert t["tutar_kurus"] == c["tutar_kurus"] and t["tarih"] == c["vade_tarihi"]
        else:
            assert not tahsilat[c["cek_id"]]


def test_banka_bakiyesi_acilis_arti_hareketler(T):
    acilis = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in T["banka_hesaplari"]}
    bakiye = dict(acilis)
    for s in sorted(T["banka_hareketleri"], key=lambda s: s["hareket_id"]):
        bakiye[s["hesap_id"]] += s["tutar_kurus"]
        assert s["bakiye_kurus"] == bakiye[s["hesap_id"]], s["hareket_id"]
        assert s["bakiye_kurus"] >= 0, s["hareket_id"]
    assert banka_bakiyeleri(T, AYAR.veri_bitis) == bakiye


def test_her_tahsilat_ve_odeme_bankada_bir_kez(T):
    kaynak = Counter((s["kaynak_tur"], s["kaynak_id"]) for s in T["banka_hareketleri"])
    for t in T["tahsilatlar"]:
        assert kaynak[("tahsilat", t["tahsilat_id"])] == 1
    for o in T["odemeler"]:
        assert kaynak[("odeme", o["odeme_id"])] == 1
    assert sum(1 for k in kaynak if k[0] == "tahsilat") == len(T["tahsilatlar"])
    assert sum(1 for k in kaynak if k[0] == "odeme") == len(T["odemeler"])


# ---------------------------------------------------------------- olaylar


def test_olay_sayisi_ayardaki_kadar(uretim):
    olaylar = uretim.anahtar["olaylar"]
    assert Counter(o["tur"] for o in olaylar) == Counter(dict(AYAR.olay_sayilari))
    assert len(olaylar) == sum(n for _, n in AYAR.olay_sayilari) == 12
    assert sum(1 for o in olaylar if dt.date.fromisoformat(o["tarih"]) > AYAR.simdi) >= 3
    for o in olaylar:
        assert uretim.veri_baslangic <= dt.date.fromisoformat(o["tarih"]) <= AYAR.veri_bitis
        assert o["simdiden_sonra"] == (dt.date.fromisoformat(o["tarih"]) > AYAR.simdi)


def test_olaylar_kapaliyken_temel_veri_ayni(uretim, temel):
    assert temel.anahtar["olaylar"] == []
    for ad in ("musteriler", "faturalar", "pos_islemleri", "tedarikciler", "banka_hesaplari", "doviz_kurlari"):
        assert tablo_hash(ad, uretim.tablolar[ad]) == tablo_hash(ad, temel.tablolar[ad]), ad
    etkilenen = defaultdict(set)
    for o in uretim.anahtar["olaylar"]:
        for ad, kimlikler in o["etkilenen"].items():
            etkilenen[ad].update(kimlikler)
    for ad in ("tahsilatlar", "cekler", "cek_hareketleri", "odemeler", "alis_faturalari"):
        anahtar = SEMA[ad][0]
        acik = {s[anahtar]: s for s in uretim.tablolar[ad] if s[anahtar] not in etkilenen[ad]}
        kapali = {s[anahtar]: s for s in temel.tablolar[ad] if s[anahtar] not in etkilenen[ad]}
        assert acik == kapali, ad


def _olaylar(uretim, tur):
    secilen = [o for o in uretim.anahtar["olaylar"] if o["tur"] == tur]
    assert len(secilen) == dict(AYAR.olay_sayilari)[tur]
    return secilen


def _a_try_en_dusuk(T, bas, bit):
    return min(s["bakiye_kurus"] for s in T["banka_hareketleri"] if s["hesap_id"] == "A_TRY" and bas <= s["tarih"] <= bit)


def test_olay_buyuk_musteri_gecikmesi_temelde_yok_olayda_var(uretim, temel):
    once = {t["tahsilat_id"]: t for t in temel.tablolar["tahsilatlar"]}
    sonra = {t["tahsilat_id"]: t for t in uretim.tablolar["tahsilatlar"]}
    for o in _olaylar(uretim, "buyuk_musteri_gecikmesi"):
        assert len(o["tahsilatlar"]) >= 3 and 45 <= o["gecikme_gun"] <= 90
        for e in o["tahsilatlar"]:
            assert once[e["tahsilat_id"]]["tarih"].isoformat() == e["eski_tarih"]
            if e["yeni_tarih"] is None:
                assert e["tahsilat_id"] not in sonra
            else:
                assert sonra[e["tahsilat_id"]]["tarih"].isoformat() == e["yeni_tarih"]
                assert (sonra[e["tahsilat_id"]]["tarih"] - once[e["tahsilat_id"]]["tarih"]).days >= 45


def test_olay_karsiliksiz_cek_temelde_tahsil_olayda_karsiliksiz(uretim, temel):
    for o in _olaylar(uretim, "karsiliksiz_cek"):
        assert cek_durumlari(temel.tablolar, AYAR.veri_bitis)[o["cek_id"]] == "tahsil_edildi"
        assert cek_durumlari(uretim.tablolar, AYAR.veri_bitis)[o["cek_id"]] == "karsiliksiz"
        assert cek_durumlari(uretim.tablolar, dt.date.fromisoformat(o["tarih"]) - dt.timedelta(days=1))[o["cek_id"]] != "karsiliksiz"


def test_olay_mukerrer_odeme_temelde_tek_olayda_cift(uretim, temel):
    for o in _olaylar(uretim, "mukerrer_odeme"):
        def odemeler(T):
            return [x for x in T["odemeler"] if x["alis_fatura_id"] == o["alis_fatura_id"]]
        assert len(odemeler(temel.tablolar)) == 1
        cift = odemeler(uretim.tablolar)
        assert len(cift) == 2 and cift[0]["tutar_kurus"] == cift[1]["tutar_kurus"] == o["tutar_kurus"]


def test_olay_olagandisi_cikis_buyuk_ve_yeni_karsi_taraf(uretim, temel):
    temel_odemeler = {x["odeme_id"] for x in temel.tablolar["odemeler"]}
    temel_aciklamalar = {x["aciklama"] for x in temel.tablolar["odemeler"]}
    tedarikci = sorted(x["tutar_kurus"] for x in temel.tablolar["odemeler"] if x["tur"] == "tedarikci")
    p95 = tedarikci[len(tedarikci) * 95 // 100]
    odemeler = {x["odeme_id"]: x for x in uretim.tablolar["odemeler"]}
    for o in _olaylar(uretim, "olagandisi_buyuk_cikis"):
        x = odemeler[o["odeme_id"]]
        assert o["odeme_id"] not in temel_odemeler and x["aciklama"] not in temel_aciklamalar
        assert x["tutar_kurus"] == o["tutar_kurus"] >= 2 * p95
        gun = dt.date.fromisoformat(o["tarih"])
        yakin = [y["tutar_kurus"] for y in uretim.tablolar["odemeler"] if abs((y["tarih"] - gun).days) <= 30]
        assert x["tutar_kurus"] == max(yakin)


def test_olay_eslesmeyen_birikim_temelde_yok_olayda_var(uretim, temel):
    for o in _olaylar(uretim, "eslesmeyen_tahsilat_birikimi"):
        bas, bit = dt.date.fromisoformat(o["tarih"]), dt.date.fromisoformat(o["bitis"])

        def sayi(T):
            return sum(1 for t in T["tahsilatlar"] if t["eslesme"] != "eslesti" and bas <= t["tarih"] <= bit)
        assert sayi(temel.tablolar) <= 1
        assert sayi(uretim.tablolar) >= sayi(temel.tablolar) + 4


def test_olay_nakit_sikismasi_temelde_yok_olayda_var_sonra_gecer(uretim, temel):
    for o in _olaylar(uretim, "nakit_sikismasi"):
        gun = dt.date.fromisoformat(o["tarih"])
        pencere = gun + dt.timedelta(days=14)
        assert _a_try_en_dusuk(temel.tablolar, gun, pencere) >= o["esik_kurus"]
        assert _a_try_en_dusuk(uretim.tablolar, gun, pencere) < o["esik_kurus"]
        assert o["esik_alti_ilk_gun"] is not None and o["esik_alti_ilk_gun"] <= pencere.isoformat()
        # Öne çekilen ödemelerin eski tarihleri geçince sıkışma biter.
        biter = dt.date.fromisoformat(o["bitis"])
        assert biter == max(dt.date.fromisoformat(x["eski_tarih"]) for x in o["odemeler"])
        assert biter < AYAR.veri_bitis and dt.date.fromisoformat(o["esik_alti_son_gun"]) <= biter


def test_kimlikler_sirasiz_olay_kayitlari_kimlikten_belli_olmaz(T):
    import re

    for ad, onek in (("tahsilatlar", "T"), ("odemeler", "O")):
        satirlar = T[ad]
        anahtar = SEMA[ad][0]
        assert all(re.fullmatch(onek + "[0-9A-F]{8}", s[anahtar]) for s in satirlar)
        tarih_sirasi = {s[anahtar]: i for i, s in enumerate(sorted(satirlar, key=lambda s: (s["tarih"], s[anahtar])))}
        kimlik_sirasi = sorted(tarih_sirasi)
        n = len(kimlik_sirasi)
        ort = (n - 1) / 2
        kov = sum((i - ort) * (tarih_sirasi[k] - ort) for i, k in enumerate(kimlik_sirasi))
        varyans = sum((i - ort) ** 2 for i in range(n))
        assert abs(kov / varyans) < 0.1, ad  # kimlik sırası ile tarih sırası ilişkisiz
    assert any(o["tur"] == "diger" for o in T["odemeler"] if o["aciklama"].split()[-1] in ("PRİMİ", "MÜŞAVİRLİK"))


def test_yan_hesap_aciklari_a_tryden_karsilanir(temel):
    T = temel.tablolar
    acilis = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in T["banka_hesaplari"]}
    assert acilis["A_TRY"] > 0 and acilis["B_TRY"] == acilis["B_EUR"] == acilis["C_USD"] == 0
    karsilama = [s for s in T["banka_hareketleri"] if s["kaynak_id"].endswith("-karsilama")]
    assert {s["hesap_id"] for s in karsilama} >= {"A_TRY", "B_TRY", "B_EUR"}
    assert all(s["bakiye_kurus"] >= 0 for s in T["banka_hareketleri"])
    # A_TRY'nin en düşük bakiyesi tampona birebir eşittir (tek turda kesin hesap).
    satis = defaultdict(int)
    for f in T["faturalar"]:
        satis[f["fatura_tarihi"].replace(day=1)] += f["tutar_try_kurus"]
    for p in T["pos_islemleri"]:
        satis[p["islem_tarihi"].replace(day=1)] += p["tutar_kurus"]
    tam = [a for a in satis if a >= temel.veri_baslangic and (a + dt.timedelta(days=32)).replace(day=1) <= AYAR.veri_bitis + dt.timedelta(days=1)]
    ortalama = sum(satis[a] for a in tam) // len(tam)
    tampon = round(ortalama * AYAR.acilis_tamponu)
    en_dusuk = min(s["bakiye_kurus"] for s in T["banka_hareketleri"] if s["hesap_id"] == "A_TRY")
    assert abs(en_dusuk - tampon) <= 1


# ---------------------------------------------------------------- as-of görünüm


def test_gorunumde_simdiden_sonrasi_yok(T):
    g = gorunum(T, AYAR.simdi)
    for ad, sutun in TARIH_SUTUNU.items():
        if sutun:
            assert all(s[sutun] <= AYAR.simdi for s in g[ad]), ad
            assert len(g[ad]) < len(T[ad]) or ad in ("banka_hesaplari", "musteriler"), ad


def test_simdiden_sonraki_olaylar_gorunumde_gizli(T, uretim):
    g = gorunum(T, AYAR.simdi)
    odeme_kimlikleri = {o["odeme_id"] for o in g["odemeler"]}
    tahsilat_kimlikleri = {t["tahsilat_id"] for t in g["tahsilatlar"]}
    durumlar = cek_durumlari(g, AYAR.simdi)
    sonraki = [o for o in uretim.anahtar["olaylar"] if o["simdiden_sonra"]]
    assert sonraki
    for o in sonraki:
        if o["tur"] == "karsiliksiz_cek":
            assert durumlar.get(o["cek_id"]) != "karsiliksiz"
        elif o["tur"] == "olagandisi_buyuk_cikis":
            assert o["odeme_id"] not in odeme_kimlikleri
        elif o["tur"] == "buyuk_musteri_gecikmesi":
            for e in o["tahsilatlar"]:
                if e["yeni_tarih"]:
                    assert e["tahsilat_id"] not in tahsilat_kimlikleri


@pytest.mark.parametrize("gun", [dt.date(2025, 3, 14), dt.date(2025, 12, 31), AYAR.simdi])
def test_acik_tutarlar_yalnizca_gecmisten_hesaplanir(T, gun):
    assert acik_tutarlar(T, gun) == acik_tutarlar(gorunum(T, gun), gun)
    assert banka_bakiyeleri(T, gun) == banka_bakiyeleri(gorunum(T, gun), gun)


# ---------------------------------------------------------------- kalibrasyon ölçümleri


def test_kalibrasyon_olcumleri_hesaplanir(T):
    olcum = kalibrasyon.olc(T, AYAR.veri_bitis, 2025)
    assert olcum["dso_gun"] > 0 and olcum["olgun_fatura_sayisi"] > 100
    assert 0 < olcum["gec_odenen_fatura_orani"] < 1
    assert abs(sum(olcum["gecikme_dagilimi"].values()) - 1) < 0.01


def test_bant_karsilastirmasi_kaynak_dosyayi_okur(T):
    import json

    kaynak = json.loads((KOK / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))
    satirlar = kalibrasyon.bantlarla_karsilastir(kalibrasyon.olc(T, AYAR.veri_bitis, 2025), kaynak)
    assert [s["ad"] for s in satirlar] == ["dso_gun", "gec_odenen_fatura_orani", "gecikme_1-30", "gecikme_31-60",
                                           "gecikme_61-90", "gecikme_90+"]
    assert all(s["alt"] <= s["ust"] for s in satirlar)


# ---------------------------------------------------------------- denetim modülü


def test_denetimler_ornek_veride_gecer(uretim):
    sonuc = denetim.hepsi(uretim.tablolar, uretim.anahtar, AYAR.olay_sayilari, AYAR.simdi, AYAR.veri_bitis)
    assert {ad: h for ad, h in sonuc.items() if h} == {}


def test_denetimler_bozuk_veriyi_yakalar(uretim):
    import copy

    T = copy.deepcopy(uretim.tablolar)
    T["banka_hareketleri"][10]["tutar_kurus"] += 1
    T["tahsilatlar"][0]["fatura_id"] = "F999999"
    T["tahsilatlar"][1]["tutar_kurus"] = 1.5
    T["odemeler"].append(dict(T["odemeler"][0], odeme_id="O9999999"))
    sonuc = denetim.hepsi(T, uretim.anahtar, AYAR.olay_sayilari, AYAR.simdi, AYAR.veri_bitis)
    for ad in ("banka_bakiye_acilis_arti_hareket", "tahsilat_faturaya_bagli_ya_da_bilerek_eslesmemis",
               "para_tamsayi_kurus", "her_tahsilat_ve_odeme_bankada_bir_kez"):
        assert sonuc[ad], ad
