"""Faz 3 kabul testi: gerçek veride araçlar, yetki ve 60 yetkisiz deneme.

Önce Faz 1 betikleri ve scripts/faz2_ambar_kur.py çalıştırılmış olmalı. Sunucu ambarı salt okunur açar;
satış rollerinin kapsam ambarları geçici bir klasöre kurulur. Kimlik kartları test anahtarıyla imzalanır.

Kabul ölçütleri:
- 60 yetkisiz denemenin hepsi beklendiği gibi; sızıntı 0. C grubundaki denemeler gerçekten saklanacak veriye
  dokunur (aynı soruya CFO farklı cevap alır). Tek istisna: çeklerin hepsi yurtiçi müşterilerinde olduğundan
  yurtiçi müdürünün çek sorusunda saklanacak bir şey yoktur.
- Satış rollerinin kapsam ambarları, Python'da ayrıca süzülüp kurulan ambarla birebir aynı; izinli araçların
  cevapları da o ambardaki cevaplarla aynı.
- Cevaplar sınırlı: en uzun cevap 3.400 karakterden, araç listesi 8.000 karakterden kısa; her çağrı 2 sn içinde.
- Her çağrı denetim kaydında; zincir sağlam.
"""

import datetime as dt
import json
import time
from pathlib import Path

import faz3_denemeler as fd
import pytest

from sayman.ambar.kurulum import baglan
from sayman.araclar.kayit import DenetimKaydi, dogrula
from sayman.araclar.kimlik import kart_uret
from sayman.araclar.sunucu import SaymanSunucusu
from sayman.araclar.yetki import yetki_oku
from sayman.veri.cikti import SEMA, csv_oku, veri_hash
from sayman.veri.saat import saat_oku

KOK = Path(__file__).resolve().parents[1]
URETILEN = KOK / "data" / "uretilen"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
SAAT = KOK / "data" / "saat.json"
ANAHTAR = bytes(range(1, 33))
ZAMAN = 1_790_000_000.0

pytestmark = pytest.mark.skipif(not (URETILEN.exists() and AMBAR.exists() and SAAT.exists()),
                                reason="Önce faz1_veri_uret.py ve faz2_ambar_kur.py çalıştırılmalı")
CFO, YURTICI, IHRACAT = fd.KULLANICILAR["cfo"], fd.KULLANICILAR["yurtici"], fd.KULLANICILAR["ihracat"]


@pytest.fixture(scope="module")
def T():
    return csv_oku(URETILEN)


@pytest.fixture(scope="module")
def saat():
    return saat_oku(SAAT)


@pytest.fixture(scope="module")
def ortam(tmp_path_factory, saat):
    klasor = tmp_path_factory.mktemp("faz3_kabul")
    s = SaymanSunucusu(ambar=AMBAR, saat=lambda: saat, anahtar=ANAHTAR, yetki=yetki_oku(),
                       kayit=DenetimKaydi(klasor / "kayit.jsonl"), kapsam_klasoru=klasor / "kapsam",
                       zaman=lambda: ZAMAN)
    yield s, klasor
    s.kapat()


@pytest.fixture(scope="module")
def sunucu(ortam):
    return ortam[0]


@pytest.fixture(scope="module")
def hakem(T, saat, tmp_path_factory):
    h = fd.Hakem(T, saat.simdi, tmp_path_factory.mktemp("hakem"))
    yield h
    h.kapat()


def kart(kullanici):
    return kart_uret(kullanici, ANAHTAR, 3600, ZAMAN)


def test_ambar_bu_csvlerden_kuruldu(T):
    con = baglan(AMBAR)
    try:
        assert con.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == \
            veri_hash(T)[0], "ambar data/uretilen ile aynı değil: scripts/faz2_ambar_kur.py yeniden çalıştırılmalı"
    finally:
        con.close()


def test_kapsam_ambarlari_python_suzgeciyle_birebir_ayni(sunucu, hakem, T):
    for kapsam in fd.KAPSAMLAR:
        tablolar = fd.kapsam_tablolari(T, kapsam)
        for ad in SEMA:
            sql = f"SELECT * FROM {ad} ORDER BY ALL"
            assert sunucu.baglantilar[kapsam].execute(sql).fetchall() == \
                hakem.referans[kapsam].execute(sql).fetchall(), (kapsam, ad)
            assert sunucu.baglantilar[kapsam].execute(f"SELECT count(*) FROM {ad}").fetchone()[0] == len(tablolar[ad])
    sayilar = {k: sunucu.baglantilar[k].execute("SELECT count(*) FROM musteriler").fetchone()[0]
               for k in ("hepsi", *fd.KAPSAMLAR)}
    assert sayilar["yurtici"] > 0 and sayilar["ihracat"] > 0 and sayilar["yurtici"] + sayilar["ihracat"] == \
        sayilar["hepsi"]


def test_60_yetkisiz_deneme_sifir_sizinti(sunucu, hakem, T, saat):
    liste = fd.denemeler(T, saat.simdi, saat.veri_bitis)
    sonuclar = fd.denemeleri_calistir(sunucu, liste, ANAHTAR, ZAMAN, hakem)
    sorunlu = [(r["no"], r["arac"], r["argumanlar"], r["sorunlar"]) for r in sonuclar if r["sorunlar"]]
    assert not sorunlu
    disarida_cek = [c for c in T["cekler"] if c["musteri_id"] in
                    {m["musteri_id"] for m in T["musteriler"] if not m["yurtici"]}]
    beklenen_bos = [] if disarida_cek else [(YURTICI, "cek_portfoyu")]
    assert [(r["kullanici"], r["arac"]) for r in sonuclar if r["grup"] == "C" and not r["gizledi"]] == beklenen_bos


def _kapsam_ici_cagrilar(sunucu, kapsam, simdi):
    con = sunucu.baglantilar[kapsam]
    musteriler = [r[0] for r in con.execute(
        "SELECT m.ad FROM musteriler m JOIN faturalar f USING (musteri_id) WHERE f.fatura_tarihi <= ? "
        "GROUP BY m.ad ORDER BY count(*) DESC, m.ad LIMIT 3", [simdi]).fetchall()]
    faturalar = [r[0] for r in con.execute(
        "SELECT fatura_id FROM faturalar WHERE fatura_tarihi <= ? ORDER BY fatura_id", [simdi]).fetchall()]
    once = (simdi - dt.timedelta(days=180)).isoformat()
    return ([("alacak_yaslandirma", {}), ("alacak_yaslandirma", {"en_fazla": 10}), ("alacak_yaslandirma", {"tarih": once}),
             ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}), ("geciken_alacaklar", {"min_gun": 90}),
             ("dso_hesapla", {"donem": "son_30_gun"}), ("dso_hesapla", {"donem": "son_90_gun"}),
             ("dso_hesapla", {"donem": "son_12_ay", "tarih": "2025-12-31"}),
             ("doviz_kuru", {"para_birimi": "USD"}), ("doviz_kuru", {"para_birimi": "GBP", "tarih": once}),
             ("cek_portfoyu", {}), ("cek_portfoyu", {"durum": "karsiliksiz", "en_fazla": 10}),
             ("cek_portfoyu", {"vade_gun": 30}), ("fatura_ara", {"durum": "acik", "en_fazla": 10}),
             ("fatura_ara", {"durum": "iade"}), ("fatura_ara", {"fatura_no": faturalar[len(faturalar) // 2]})]
            + [("musteri_ozeti", {"musteri_adi": ad}) for ad in musteriler]
            + [("alacak_yaslandirma", {"musteri_adi": musteriler[0]}),
               ("fatura_ara", {"musteri_adi": musteriler[1], "durum": "kapali"})])


@pytest.mark.parametrize("kullanici", [YURTICI, IHRACAT])
def test_satis_rolu_izinli_araclarda_kapsamdaki_veriyle_ayni_cevap(sunucu, hakem, saat, kullanici):
    kapsam = sunucu.yetki.roller[sunucu.yetki.kullanicilar[kullanici]].kapsam
    cagrilar = _kapsam_ici_cagrilar(sunucu, kapsam, saat.simdi)
    # belge_ara (Faz 6) ambara değil belge kütüphanesine bakar; rol süzmesi test_faz6_belge_araci.py'de sınanır.
    assert {a for a, _ in cagrilar} == \
        set(sunucu.yetki.roller[sunucu.yetki.kullanicilar[kullanici]].araclar) - {"belge_ara"}
    cevaplar = fd.mcp_cagir(sunucu, [(a, args, True, kart(kullanici)) for a, args in cagrilar])
    for (arac, args), (yanit, hata) in zip(cagrilar, cevaplar):
        deneme = {"arac": arac, "argumanlar": args, "beklenen": ("kapsamli",)}
        assert hakem.degerlendir(deneme, kapsam, yanit, hata, yansima_haric=True) == [], (arac, args)
        assert not hata, (arac, args, yanit)


def test_cevaplar_ve_arac_listesi_sinirli_ve_hizli(sunucu, saat):
    araclar = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                 "parameters": t.inputSchema}} for t in sunucu.araclar()]
    # Faz 9 üçüncü kol cek_portfoyu'na musteri_adi ekledi (+116 karakter; öncesi 7.986). Asıl sınır token bütçesi
    # (orkestrator.Ayar.arac_listesi_token = 2400); karakter sınırı onun kaba vekili.
    assert len(json.dumps(araclar, ensure_ascii=False)) < 8200
    en_cok = sunucu.baglantilar["hepsi"].execute(
        "SELECT m.ad FROM faturalar f JOIN musteriler m USING (musteri_id) GROUP BY m.ad "
        "ORDER BY count(*) DESC, m.ad LIMIT 1").fetchone()[0]
    olcumler = []
    for kullanici in (CFO, YURTICI, IHRACAT):
        izinli = sunucu.yetki.roller[sunucu.yetki.kullanicilar[kullanici]].araclar
        for arac, args in fd.en_genis_cagrilar(saat.simdi, en_cok):
            if arac not in izinli:
                continue
            bas = time.perf_counter()
            yanit, hata = fd.mcp_cagir(sunucu, [(arac, args, True, kart(kullanici))])[0]
            sure = time.perf_counter() - bas
            assert not hata, (kullanici, arac, yanit)
            olcumler.append((len(json.dumps(yanit, ensure_ascii=False)), sure, kullanici, arac, args.get("en_fazla")))
    # 3.400 karakter ~1.800 token (reports/faz3_araclar.json'da qwen3.5:9b ile ölçülür): araç listesi, sistem istemi
    # ve cevap payından sonra 8.192'lik pencereye en uzun cevaptan iki tane rahat sığar; üçüncüsü sınırda.
    assert max(o[0] for o in olcumler) < 3400, max(olcumler)
    assert max(o[1] for o in olcumler) < 2.0, max(olcumler, key=lambda o: o[1])


def test_her_cagri_denetim_kaydinda_ve_zincir_saglam(ortam):
    s, klasor = ortam
    dosyadaki = DenetimKaydi.oku(klasor / "kayit.jsonl")
    assert len(dosyadaki) == len(s.kayit.satirlar) > 60 and dogrula(dosyadaki) == []
    metin = (klasor / "kayit.jsonl").read_text(encoding="utf-8")
    for k in (CFO, YURTICI, IHRACAT):
        assert kart(k).split(".")[1] not in metin


def test_faturasindan_once_odenen_tutar_gelecekteki_faturayi_gostermez(sunucu):
    """Nakit sıkışması olayında bazı tedarikçi ödemeleri faturasından önce yapılmış. Ödeme günüyle fatura günü
    arasındaki her günde, CFO'nun araçları henüz kesilmemiş faturanın numarasını göstermemeli."""
    from sayman.araclar.araclar import ARACLAR_ADA_GORE, Baglam

    con = sunucu.baglantilar["hepsi"]
    ornekler = con.execute("""
        SELECT o.tarih, a.alis_fatura_id, a.tarih FROM odemeler o JOIN alis_faturalari a USING (alis_fatura_id)
        WHERE o.tarih < a.tarih ORDER BY o.tarih, a.alis_fatura_id""").fetchall()
    assert len(ornekler) == 34
    for gun in sorted({o[0] for o in ornekler}):
        gelecek = {aid for _, aid, ft in ornekler if ft > gun} | {
            r[0] for r in con.execute("SELECT alis_fatura_id FROM alis_faturalari WHERE tarih > ?", [gun]).fetchall()}
        b = Baglam(con, gun)
        for arac, args in [("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}),
                           ("banka_hareketleri", {"baslangic": gun.isoformat(), "bitis": gun.isoformat(), "en_fazla": 10})]:
            _, sonuc, kaynak = ARACLAR_ADA_GORE[arac].calistir(b, args)
            metin = json.dumps([sonuc, kaynak], ensure_ascii=False)
            assert not [aid for aid in gelecek if aid in metin], (gun, arac)
