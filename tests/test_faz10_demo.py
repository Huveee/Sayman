"""Faz 10 kabul testleri: demo arka ucu (FastAPI) betikli bir modelle.

Kabul ölçütleri (FAZLAR.md): CFO ve satış müdürü aynı soruya farklı cevap alır; zamanı ileri sarmak raporu ve
uyarıyı tetikler. Ayrıca: kimlik yalnızca sunucu oturumundan gelir (gövdedeki kullanıcı yok sayılır, oturumsuz
istek 401), uyarılar rol kapsamına göre süzülür, rapor yalnızca rolün araçlarından kurulur, ambar ve data/saat.json
demo boyunca değişmez.

İki ortam: Faz 2'nin elle hesaplanmış küçük şirketi (hızlı API testleri) ve gerçek veri (zaman ve uyarı testleri;
veri yoksa atlanır).
"""

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import faz3_denemeler as fd
import pytest
from fastapi.testclient import TestClient
from test_faz2_ambar import D, _elle_tablolar

from sayman.ambar.kurulum import ambar_kur
from sayman.araclar.yetki import yetki_oku
from sayman.asistan.orkestrator import ModelCevabi
from sayman.demo.uygulama import CEREZ, Demo, tl, uygulama_kur
from sayman.veri.cikti import csv_oku, csv_yaz
from sayman.veri.saat import Saat, saat_oku, saat_yaz

KOK = Path(__file__).resolve().parents[1]
ANAHTAR = bytes(range(32))
CFO, YURTICI, IHRACAT = fd.KULLANICILAR["cfo"], fd.KULLANICILAR["yurtici"], fd.KULLANICILAR["ihracat"]
SORU = "Vadesi geçmiş alacaklarımızın toplamı ne kadar?"
GERCEK_AMBAR, GERCEK_SAAT, URETILEN = (KOK / "data" / "ambar" / "sayman.duckdb", KOK / "data" / "saat.json",
                                       KOK / "data" / "uretilen")


class BetikliModel:
    """Soruda "nakit" geçiyorsa nakit_pozisyonu, yoksa alacak_yaslandirma çağırır; araç sonucundaki rakamı yazar."""

    def __init__(self):
        self.cagri = 0

    def __call__(self, mesajlar, araclar, zaman_asimi):
        self.cagri += 1
        if mesajlar[-1]["role"] != "tool":
            ad = "nakit_pozisyonu" if "nakit" in mesajlar[1]["content"].lower() else "alacak_yaslandirma"
            return ModelCevabi(arac_cagrilari=[(ad, {})], istem_token=100, uretim_token=10)
        yanit = json.loads(mesajlar[-1]["content"])
        sonuc, kapsam = yanit["sonuc"], yanit["kapsam"]
        if "hesaplar" in sonuc:
            return ModelCevabi(icerik=f"Bankalardaki toplam nakit {tl(sonuc['toplam_tl'])}.")
        return ModelCevabi(icerik=f"Vadesi geçmiş alacaklarınızın toplamı {tl(sonuc['vadesi_gecmis_tl'])} "
                                  f"({kapsam}).")


def _demo(klasor, ambar, baslangic, tablolar, **ek):
    return Demo(ambar=ambar, durum_klasoru=klasor / "demo", baslangic=baslangic, anahtar=ANAHTAR, yetki=yetki_oku(),
                tablolar=tablolar, model=BetikliModel(), zaman=lambda: 1_750_000_000.0, **ek)


def _giris(app, kullanici) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/giris", json={"kullanici": kullanici})
    assert r.status_code == 200 and r.json()["kullanici"] == kullanici
    return c


@pytest.fixture(scope="module")
def elle(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("faz10_elle")
    tablolar = _elle_tablolar()
    csv_yaz(tablolar, klasor / "csv")
    ambar_kur(klasor / "csv", klasor / "sayman.duckdb").close()
    demo = _demo(klasor, klasor / "sayman.duckdb", Saat(D, dt.date(2025, 6, 30)), tablolar)
    yield demo, uygulama_kur(demo)
    demo.kapat()


# ------------------------------------------------------------------ kimlik ve oturum


@pytest.mark.parametrize("yol,yontem", [("/api/ben", "get"), ("/api/rapor", "get"), ("/api/uyarilar", "get"),
                                        ("/api/ornek_sorular", "get"), ("/api/sor", "post"),
                                        ("/api/ileri_sar", "post")])
def test_oturumsuz_istek_401(elle, yol, yontem):
    c = TestClient(elle[1])
    r = c.post(yol, json={"soru": SORU}) if yontem == "post" else c.get(yol)
    assert r.status_code == 401
    c.cookies.set(CEREZ, "uydurma-oturum")
    r = c.post(yol, json={"soru": SORU}) if yontem == "post" else c.get(yol)
    assert r.status_code == 401


def test_giris_bilinmeyen_kullanici_ve_httponly_cerez(elle):
    c = TestClient(elle[1])
    assert c.post("/api/giris", json={"kullanici": "kimse"}).status_code == 400
    r = c.post("/api/giris", json={"kullanici": CFO})
    cerez = r.headers["set-cookie"]
    assert "httponly" in cerez.lower() and "samesite=strict" in cerez.lower()
    # Tarayıcıya ne anahtar ne kart gider
    assert ANAHTAR.hex() not in r.text and "kart" not in r.json()
    assert c.post("/api/cikis").json() == {"tamam": True}
    assert c.get("/api/ben").status_code == 401


def test_kullanici_listesi_ve_sayfa(elle):
    c = TestClient(elle[1])
    kisiler = c.get("/api/kullanicilar").json()["kullanicilar"]
    assert {k["kullanici"] for k in kisiler} == {CFO, YURTICI, IHRACAT}
    assert "Sayman" in c.get("/").text and c.get("/static/app.js").status_code == 200


# ------------------------------------------------------------------ kabul: aynı soru, farklı cevap


def test_cfo_ve_satis_muduru_ayni_soruya_farkli_cevap_alir(elle):
    demo, app = elle
    cevaplar = {}
    for k in (CFO, YURTICI, IHRACAT):
        r = _giris(app, k).post("/api/sor", json={"soru": SORU})
        assert r.status_code == 200
        j = r.json()
        assert j["durum"] == "tamam" and j["araclar"] == [{"arac": "alacak_yaslandirma", "hata": None}]
        beklenen, hata = demo.sunucu.kapi("alacak_yaslandirma", {}, demo.kart(k))
        assert not hata and tl(beklenen["sonuc"]["vadesi_gecmis_tl"]) in j["cevap"]
        cevaplar[k] = j["cevap"]
    assert len(set(cevaplar.values())) == 3


def test_govdedeki_kullanici_yok_sayilir(elle):
    app = elle[1]
    yurtici = _giris(app, YURTICI).post("/api/sor", json={"soru": SORU}).json()["cevap"]
    sahte = _giris(app, YURTICI).post("/api/sor", json={"soru": SORU, "kullanici": CFO, "rol": "cfo"}).json()
    cfo = _giris(app, CFO).post("/api/sor", json={"soru": SORU}).json()["cevap"]
    assert sahte["cevap"] == yurtici != cfo


def test_satis_muduru_nakit_sorusunda_yetki_yok(elle):
    app = elle[1]
    j = _giris(app, YURTICI).post("/api/sor", json={"soru": "Bankalarda ne kadar nakit var?"}).json()
    assert j["durum"] == "ret" and j["araclar"] == [{"arac": "nakit_pozisyonu", "hata": "yetki_yok"}]
    assert _giris(app, CFO).post("/api/sor", json={"soru": "Bankalarda ne kadar nakit var?"}).json()["durum"] == "tamam"


def test_soru_dogrulama_ve_mesgulken_409(elle):
    demo, app = elle
    c = _giris(app, CFO)
    assert c.post("/api/sor", json={"soru": "  "}).status_code == 422
    assert c.post("/api/sor", json={"soru": "x" * 501}).status_code == 422
    assert demo.kilit.acquire(blocking=False)
    try:
        r = c.post("/api/sor", json={"soru": SORU})
        assert r.status_code == 409 and c.get("/api/durum").json()["mesgul"] is True
    finally:
        demo.kilit.release()


# ------------------------------------------------------------------ rapor ve uyarı kapsamı


def test_rapor_yalniz_rolun_araclarindan(elle):
    app = elle[1]
    cfo = _giris(app, CFO).get("/api/rapor").json()
    satis = _giris(app, YURTICI).get("/api/rapor").json()
    assert [b["baslik"] for b in cfo["bolumler"]] == ["Nakit pozisyonu", "Önümüzdeki 7 günün ödemeleri",
                                                      "Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    assert [b["baslik"] for b in satis["bolumler"]] == ["Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    assert cfo["tarih"] == satis["tarih"] == D.isoformat() and satis["kapsam_adi"] == "yalnızca yurtiçi müşteriler"
    assert all(b["ozet"] != "—" for b in cfo["bolumler"] + satis["bolumler"])  # hiçbir bölüm kapıda reddedilmedi
    assert cfo["bolumler"][2]["ozet"] != satis["bolumler"][0]["ozet"]


def test_uyarilar_kapsama_gore_suzulur(elle):
    demo = elle[0]
    musteriler = _elle_tablolar()["musteriler"]  # süzgecin kaynağından (ambar) bağımsız
    yurtici = next(m["musteri_id"] for m in musteriler if m["yurtici"])
    ihracat = next(m["musteri_id"] for m in musteriler if not m["yurtici"])
    uyarilar = [{"uyari_id": "U1", "gun": "2025-03-01", "onem": "uyari", "baslik": "a", "aciklama": "a",
                 "musteri_id": yurtici, "kaynak": {"x": [1]}},
                {"uyari_id": "U2", "gun": "2025-03-02", "onem": "uyari", "baslik": "b", "aciklama": "b",
                 "musteri_id": ihracat},
                {"uyari_id": "U3", "gun": "2025-03-03", "onem": "bilgi", "baslik": "c", "aciklama": "c",
                 "musteri_id": None},
                {"uyari_id": "U4", "gun": "2025-03-04", "onem": "bilgi", "baslik": "d", "aciklama": "d",
                 "musteri_id": "M_BILINMEYEN"}]
    assert [u["uyari_id"] for u in demo.suz(CFO, uyarilar)] == ["U1", "U2", "U3", "U4"]
    assert [u["uyari_id"] for u in demo.suz(YURTICI, uyarilar)] == ["U1"]
    assert [u["uyari_id"] for u in demo.suz(IHRACAT, uyarilar)] == ["U2"]
    assert "kaynak" not in demo.suz(CFO, uyarilar)[0] and "kural" not in demo.suz(CFO, uyarilar)[0]


def test_satis_aciklamasinda_sirket_geneli_rakam_yok():
    from sayman.demo.uygulama import kapsamli_aciklama
    cek = ("Ada Ltd. müşterisinin Banka G çeki (41.931,43 TL, vade 2026-09-03) karşılıksız çıktı; son 12 ayın "
           "çeklerinin üst çeyreği 27.203,60 TL'den başlıyor.")
    assert kapsamli_aciklama("karsiliksiz_cek", cek, "b") == ("Ada Ltd. müşterisinin Banka G çeki (41.931,43 TL, "
                                                             "vade 2026-09-03) karşılıksız çıktı.")
    gec = "Ada Ltd. son 12 ayın en büyük 50 müşterisinden biri. Vadesi son 30 günde dolan 4 faturasından 3 tanesi açık."
    assert kapsamli_aciklama("musteri_gecikmesi", gec, "b") == "Vadesi son 30 günde dolan 4 faturasından 3 tanesi açık."
    assert kapsamli_aciklama("yeni_kural", "şirket geneli 1.000 TL", "Başlık") == "Başlık."


def test_baska_adla_gelen_istek_reddedilir(elle):
    assert TestClient(elle[1], base_url="http://saldirgan.example").get("/api/kullanicilar").status_code == 400
    assert TestClient(elle[1], base_url="http://127.0.0.1:8010").get("/api/kullanicilar").status_code == 200


# ------------------------------------------------------------------ kabul: zamanı ileri sar (gerçek veri)

gercek_var = pytest.mark.skipif(not (GERCEK_AMBAR.exists() and GERCEK_SAAT.exists() and URETILEN.exists()),
                                reason="Önce Faz 1-2 betikleri çalıştırılmalı")


def _ozet(yol: Path) -> str:
    return hashlib.sha256(yol.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def gercek(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("faz10_gercek")
    once = (_ozet(GERCEK_AMBAR), _ozet(GERCEK_SAAT))
    demo = _demo(klasor, GERCEK_AMBAR, saat_oku(GERCEK_SAAT), csv_oku(URETILEN))
    yield demo, uygulama_kur(demo), once, klasor
    demo.kapat()


@gercek_var
def test_zamani_ileri_sarmak_rapor_ve_uyari_uretir(gercek):
    demo, app, once, klasor = gercek
    bas = demo.saat.simdi
    c = _giris(app, CFO)
    rapor_once = c.get("/api/rapor").json()
    depodaki = len(demo.depo.uyarilar())
    r = c.post("/api/ileri_sar", json={})
    assert r.status_code == 200
    j = r.json()
    assert j["onceki"] == bas.isoformat() and j["bugun"] == (bas + dt.timedelta(days=7)).isoformat()
    # Yeni uyarı: yalnızca ilerlenen günlerden (depo açılışta bugüne kadar dolduruldu)
    assert j["yeni_uyarilar"] and all(bas.isoformat() < u["gun"] <= j["bugun"] for u in j["yeni_uyarilar"])
    assert len(demo.depo.uyarilar()) == depodaki + len(j["yeni_uyarilar"])
    # Rapor yeni günün raporu
    assert j["rapor"]["tarih"] == j["bugun"] != rapor_once["tarih"]
    assert j["rapor"]["bolumler"] != rapor_once["bolumler"]
    assert c.get("/api/durum").json()["bugun"] == j["bugun"]
    yeni_idler = {u["uyari_id"] for u in j["yeni_uyarilar"]}
    assert yeni_idler <= {u["uyari_id"] for u in c.get("/api/uyarilar").json()["uyarilar"]}
    # Satış müdürleri yalnızca kendi müşterilerinin uyarılarını görür; şirket düzeyindeki uyarıları ve şirket
    # geneli eşikleri görmez. Doğruluk ölçütü süzgecin kaynağından (ambar) bağımsız: üretilen CSV tabloları.
    csv_yurtici = {m["musteri_id"]: m["yurtici"] for m in demo.tablolar["musteriler"]}
    yurtici_uyarilari = _giris(app, YURTICI).get("/api/uyarilar").json()["uyarilar"]
    assert yurtici_uyarilari
    for k, yurtici_mi, liste in ((YURTICI, True, yurtici_uyarilari),
                                 (IHRACAT, False, _giris(app, IHRACAT).get("/api/uyarilar").json()["uyarilar"])):
        for u in liste:
            assert u["musteri_id"] is not None and csv_yurtici[u["musteri_id"]] is yurtici_mi
            assert "üst çeyreği" not in u["aciklama"] and "en büyük" not in u["aciklama"]
    # Satış müdürü de zamanı ileri sarabilir; ona dönen yeni uyarılar ve rapor kendi kapsamında
    j2 = _giris(app, YURTICI).post("/api/ileri_sar", json={}).json()
    assert all(csv_yurtici[u["musteri_id"]] is True for u in j2["yeni_uyarilar"])
    assert [b["baslik"] for b in j2["rapor"]["bolumler"]] == ["Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    # Ambar ve proje saati değişmedi; demo saati kendi klasöründe
    assert (_ozet(GERCEK_AMBAR), _ozet(GERCEK_SAAT)) == once
    assert saat_oku(klasor / "demo" / "saat.json").simdi.isoformat() == j2["bugun"]


@gercek_var
def test_saat_veri_sonunda_durur_ve_yeniden_acilista_korunur(gercek):
    demo, app, _, klasor = gercek
    c = _giris(app, CFO)
    assert c.post("/api/ileri_sar", json={"gun": 0}).status_code == 422
    while demo.saat.simdi < demo.saat.veri_bitis:
        assert c.post("/api/ileri_sar", json={"gun": 31}).status_code == 200
    r = c.post("/api/ileri_sar", json={})
    assert r.status_code == 409 and "sıfırla" in r.json()["detail"]
    # Yeniden açılan demo kaldığı günden devam eder, eski uyarıları yeni diye getirmez. Saat dosyası depodan geride
    # kalsa bile (depo yazıldı, saat yazılamadan kesildi) saat depoya yetişir.
    demo.kapat()
    saat_yaz(saat_oku(GERCEK_SAAT), klasor / "demo" / "saat.json")
    ikinci = _demo(klasor, GERCEK_AMBAR, saat_oku(GERCEK_SAAT), demo.tablolar)
    try:
        assert ikinci.saat == demo.saat and len(ikinci.depo.uyarilar()) == len(demo.depo.uyarilar())
    finally:
        ikinci.kapat()
