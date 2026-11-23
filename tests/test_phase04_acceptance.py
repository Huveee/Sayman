"""Faz 4 kabul testi: gerçek veride (tohum 42) izleme kuralları ve zamanlayıcı.

Önce Faz 1 betiği ve scripts/phase04_generate_data.py çalıştırılmış olmalı.

Kabul ölçütleri:
- Gömülü olayların en az %90'ı yakalanır (data/uretilen, cevap anahtarı data/anahtar/olaylar.json).
- Olaysız veride (aynı şirketin olaysız ikizi) ayda en fazla 2 yanlış alarm.
- Aynı saatle iki çalışma aynı sonucu verir.
Ayrıca:
- Saat gün gün de ilerlese tek seferde de ilerlese depoda aynı uyarılar olur.
- Kurallar geleceği görmez: veri bitişindeki çalışmanın d gününe kadarki uyarıları, d gününde yapılan
  çalışmanın uyarılarıyla aynıdır.
- İzleme kodu cevap anahtarını okumaz.
- Olaysız ikiz, olaylı veriden yalnızca cevap anahtarında listelenen satırlarda (ve onlardan türeyen banka
  hareketlerinde) ayrılır.
- Her kural kendi koşuluna tepki verir: koşul elle eklenince uyarı gelir, koşulun yarısı eksikse gelmez.
"""

import builtins
import copy
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import support.phase04_scoring as puan
import pytest

from sayman.monitoring.scheduler import Depo, baslangic_gunu, calistir, izle
from sayman.data.table_io import SEMA, csv_oku
from sayman.data.clock import Saat, saat_oku

KOK = Path(__file__).resolve().parents[1]
URETILEN = KOK / "data" / "uretilen"
ANAHTAR = KOK / "data" / "anahtar" / "olaylar.json"
TEMIZ = KOK / "data" / "izleme_veri" / "tohum42_temiz"
SAAT = KOK / "data" / "saat.json"
G = dt.date.fromisoformat

pytestmark = pytest.mark.skipif(not (URETILEN.exists() and ANAHTAR.exists() and TEMIZ.exists() and SAAT.exists()),
                                reason="Önce phase01_generate_data.py ve phase04_generate_data.py çalıştırılmalı")


@pytest.fixture(scope="module")
def bitis():
    return saat_oku(SAAT).veri_bitis


@pytest.fixture(scope="module")
def T():
    return csv_oku(URETILEN)


@pytest.fixture(scope="module")
def T_temiz():
    return csv_oku(TEMIZ)


@pytest.fixture(scope="module")
def olaylar():
    return json.loads(ANAHTAR.read_text(encoding="utf-8"))["olaylar"]


@pytest.fixture(scope="module")
def tum(T, bitis):
    bas = time.perf_counter()
    u = izle(T, bitis)
    return u, time.perf_counter() - bas


def _ozet(uyarilar):
    return hashlib.sha256(json.dumps(uyarilar, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- kabul ölçütleri


def test_gomulu_olaylarin_yuzde_90i_yakalanir(tum, olaylar, bitis):
    p = puan.puanla(tum[0], olaylar, bitis)
    kacan = [r["olay_id"] + " " + r["tur"] for r in p["olaylar"] if not r["yakalandi"]]
    assert p["olay"] == 12
    assert p["geri_cagirma"] >= 0.90, f"kaçan olaylar: {kacan}"


def test_olaysiz_veride_ayda_en_fazla_2_yanlis_alarm(T_temiz, bitis):
    y = puan.yanlis_alarm(izle(T_temiz, bitis), baslangic_gunu(T_temiz), bitis)
    assert y["aylik"] <= 2.0, y


def test_ayni_saat_ayni_sonuc(T, tum, bitis):
    assert _ozet(izle(T, bitis)) == _ozet(tum[0])


def test_ayni_saat_ayni_sonuc_baska_surecte(tum, bitis):
    """Python'un küme ve sözlük sırasını etkileyen karma tohumu değişse de sonuç aynı."""
    betik = ("import json,hashlib,sys,datetime as dt; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
             "from sayman.data.table_io import csv_oku; from sayman.monitoring.scheduler import izle;"
             "u = izle(csv_oku(Path(sys.argv[2])), dt.date.fromisoformat(sys.argv[3]));"
             "print(hashlib.sha256(json.dumps(u, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest())")
    ozetler = set()
    for tohum in ("0", "4242"):
        ortam = {**os.environ, "PYTHONHASHSEED": tohum}
        cikti = subprocess.run([sys.executable, "-I", "-c", betik, str(KOK / "src"), str(URETILEN), bitis.isoformat()],
                               capture_output=True, text=True, env=ortam, timeout=300, check=True)
        ozetler.add(cikti.stdout.strip())
    assert ozetler == {_ozet(tum[0])}


def test_tam_calisma_hizli(tum):
    assert tum[1] < 10, f"{tum[1]:.1f} s"


# ---------------------------------------------------------------- zamanlayıcı


ADIMLAR = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-03-01", "2025-06-01", "2025-06-02", "2025-06-17",
           "2025-06-25", "2025-06-26", "2025-06-27", "2025-09-15", "2025-09-16", "2025-09-17", "2026-01-29",
           "2026-01-30", "2026-04-27", "2026-04-28", "2026-07-09", "2026-07-10", "2026-11-03", "2026-11-04",
           "2026-11-05", "2026-11-12"]


def test_saat_adim_adim_ilerlese_de_ayni_uyarilar(T, tum, bitis, tmp_path):
    depo = Depo(tmp_path / "izleme")
    for gun in ADIMLAR:
        calistir(T, Saat(G(gun), bitis), depo)
    assert depo.uyarilar() == tum[0]
    once = depo.uyari_yolu.read_bytes()
    assert calistir(T, Saat(bitis, bitis), depo)["yeni"] == []
    assert depo.uyari_yolu.read_bytes() == once


def test_tek_seferde_ilerleyen_depo_ayni(T, tum, bitis, tmp_path):
    depo = Depo(tmp_path / "izleme")
    calistir(T, Saat(bitis, bitis), depo)
    assert depo.uyarilar() == tum[0]


@pytest.mark.parametrize("gun", ["2025-02-10", "2025-06-26", "2025-09-16", "2026-01-30", "2026-04-28",
                                 "2026-09-15", "2026-11-04"])
def test_kurallar_gelecegi_gormez(T, tum, gun):
    d = G(gun)
    assert izle(T, d) == [u for u in tum[0] if G(u["gun"]) <= d]


def test_uyari_kaynaklari_o_gun_gorunen_kayitlar(T, tum):
    """Her uyarının dayandığı kayıtlar verinin içinde ve uyarının gününde (ya da önce) gerçekleşmiş."""
    tarih = {"faturalar": "fatura_tarihi", "tahsilatlar": "tarih", "cekler": "alis_tarihi", "cek_hareketleri": "tarih",
             "alis_faturalari": "tarih", "odemeler": "tarih", "banka_hareketleri": "tarih", "musteriler": None}
    dizin = {ad: {s[SEMA[ad][0]]: s for s in T[ad]} for ad in tarih}
    for u in tum[0]:
        assert u["onem"] in ("uyari", "bilgi") and u["aciklama"] and u["baslik"]
        for ad, kimlikler in u["kaynak"].items():
            assert kimlikler, u["uyari_id"]
            for k in kimlikler:
                assert k in dizin[ad], (u["uyari_id"], ad, k)
                if tarih[ad]:
                    assert dizin[ad][k][tarih[ad]] <= G(u["gun"]), (u["uyari_id"], ad, k)
    assert len({u["uyari_id"] for u in tum[0]}) == len(tum[0])


# ---------------------------------------------------------------- cevap anahtarı


def test_izleme_kodu_cevap_anahtarini_okumaz(monkeypatch, bitis, tmp_path):
    okunan = []
    asil = io.open

    def izli_open(dosya, *a, **k):
        okunan.append(str(dosya))
        return asil(dosya, *a, **k)

    monkeypatch.setattr(builtins, "open", izli_open)
    monkeypatch.setattr(io, "open", izli_open)
    t = csv_oku(URETILEN)
    calistir(t, Saat(bitis, bitis), Depo(tmp_path / "izleme"))
    monkeypatch.undo()
    assert any(o.endswith("faturalar.csv") for o in okunan)
    assert not [o for o in okunan if "anahtar" in o.replace("\\", "/").split("/") or o.endswith("olaylar.json")]


def test_izleme_kaynaginda_anahtar_yolu_yok():
    for dosya in (KOK / "src" / "sayman" / "monitoring").glob("*.py"):
        metin = dosya.read_text(encoding="utf-8")
        # Olay üreticisi gömdüğü tahsilatlara "bilerek_eslesmemis" yazar; kurallar eşleşme sütununa hiç bakmaz.
        for yasak in ("olaylar.json", "data/anahtar", "phase04_scoring", "izleme_veri", "musteri_profilleri",
                      "bilerek_eslesmemis", '"eslesme"'):
            assert yasak not in metin, (dosya.name, yasak)


def test_olaysiz_ikiz_yalnizca_anahtardaki_satirlarda_ayrilir(T, T_temiz, olaylar):
    etkilenen: dict[str, set] = {}
    for o in olaylar:
        for ad, kimlikler in o["etkilenen"].items():
            etkilenen.setdefault(ad, set()).update(kimlikler)
    hepsi = set().union(*etkilenen.values())
    for ad, sutunlar in SEMA.items():
        pk = sutunlar[0]
        if ad == "banka_hareketleri":
            # Hareketler türetilmiş: numaraları sıra numarası, bakiye sütunu birikimli. İçerikle karşılaştırılır;
            # ayrılan her hareket anahtardaki bir tahsilat ya da ödemeden gelmeli.
            def icerik(s):
                return (s["hesap_id"], s["tarih"], s["tutar_kurus"], s["aciklama"], s["kaynak_tur"], s["kaynak_id"])
            a, b = Counter(map(icerik, T[ad])), Counter(map(icerik, T_temiz[ad]))
            ayrilan = (a - b) + (b - a)
            assert ayrilan and {x[5] for x in ayrilan} <= hepsi, ad
            continue
        a, b = {s[pk]: s for s in T[ad]}, {s[pk]: s for s in T_temiz[ad]}
        ayrilan = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        assert ayrilan <= etkilenen.get(ad, set()), (ad, sorted(ayrilan - etkilenen.get(ad, set()))[:5])


# ---------------------------------------------------------------- her kural kendi koşuluna tepki verir


def _uyarilar(t, bas, bit, kural):
    return [u for u in izle(t, bit) if u["kural"] == kural and bas <= G(u["gun"]) <= bit]


def test_mukerrer_odeme_elle_eklenince_yakalanir(T_temiz):
    t = copy.deepcopy(T_temiz)
    asil = next(o for o in sorted(t["odemeler"], key=lambda o: (o["tarih"], o["odeme_id"]))
                if o["tur"] == "tedarikci" and o["tarih"] >= G("2026-03-02"))
    kopya = {**asil, "odeme_id": "OTEST0001", "tarih": asil["tarih"] + dt.timedelta(days=1)}
    t["odemeler"].append(kopya)
    u = _uyarilar(t, kopya["tarih"], kopya["tarih"] + dt.timedelta(days=3), "mukerrer_odeme")
    assert [(x["varlik"]["id"], x["gun"]) for x in u] == [(asil["alis_fatura_id"], kopya["tarih"].isoformat())]
    assert "OTEST0001" in u[0]["kaynak"]["odemeler"]


def test_buyuk_cikis_yalnizca_yeni_karsi_taraf_ve_buyuk_tutarda(T_temiz):
    gun = G("2026-03-10")
    gecmis = sorted(o["tutar_kurus"] for o in T_temiz["odemeler"]
                    if o["tur"] == "tedarikci" and gun - dt.timedelta(days=365) <= o["tarih"] < gun)
    p95 = gecmis[int(len(gecmis) * 0.95)]
    eski = next(o["aciklama"] for o in T_temiz["odemeler"] if o["tur"] == "diger" and o["tarih"] < gun)
    sablon = {"tur": "diger", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
              "iade_fatura_id": None, "tarih": gun, "yontem": "havale", "banka_hesap_id": "A_TRY"}
    t = copy.deepcopy(T_temiz)
    t["odemeler"] += [
        {**sablon, "odeme_id": "OTEST1", "tutar_kurus": 3 * p95, "aciklama": "EFT GİDEN DENEME KARŞI TARAF A"},
        {**sablon, "odeme_id": "OTEST2", "tutar_kurus": 3 * p95, "aciklama": eski},
        {**sablon, "odeme_id": "OTEST3", "tutar_kurus": p95 // 2, "aciklama": "EFT GİDEN DENEME KARŞI TARAF B"},
    ]
    u = _uyarilar(t, gun, gun, "buyuk_cikis")
    assert [x["varlik"]["id"] for x in u] == ["OTEST1"]


def test_karsiliksiz_cek_tutara_gore_uyari_ya_da_bilgi(T_temiz):
    gun0 = G("2026-02-01")
    cekler = {c["cek_id"]: c for c in T_temiz["cekler"]}
    gecmis = sorted(c["tutar_kurus"] for c in T_temiz["cekler"] if gun0 - dt.timedelta(days=364) <= c["alis_tarihi"] <= gun0)
    p75 = gecmis[int(len(gecmis) * 0.75)]
    adaylar = sorted((h for h in T_temiz["cek_hareketleri"]
                      if h["durum"] == "tahsil_edildi" and G("2026-02-01") <= h["tarih"] <= G("2026-02-28")),
                     key=lambda h: (h["tarih"], h["cek_hareket_id"]))
    buyuk = next(h for h in adaylar if cekler[h["cek_id"]]["tutar_kurus"] >= 1.5 * p75)
    kucuk = next(h for h in adaylar if cekler[h["cek_id"]]["tutar_kurus"] <= 0.5 * p75)
    t = copy.deepcopy(T_temiz)
    for h in t["cek_hareketleri"]:
        if h["cek_hareket_id"] in (buyuk["cek_hareket_id"], kucuk["cek_hareket_id"]):
            h["durum"] = "karsiliksiz"
    u = {x["varlik"]["id"]: x["onem"] for x in _uyarilar(t, G("2026-02-01"), G("2026-02-28"), "karsiliksiz_cek")}
    assert u[buyuk["cek_id"]] == "uyari" and u[kucuk["cek_id"]] == "bilgi"
