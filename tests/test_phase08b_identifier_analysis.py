"""Faz 8b kimlik ölçümü: aday numara çıkarma, etiketler, "hangi kural durdurdu" ve uydurma_kimlik kontrolü."""

import datetime as dt
import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "scripts"))
sys.path.insert(0, str(KOK / "tests"))

import support.phase07_questions as f7  # noqa: E402
from phase08b_identifier_analysis import _taslak_okuma, adaylar, bicim, etiketle  # noqa: E402

from sayman.assistant.verifier import dogrula  # noqa: E402
from sayman.evaluation.oracle import uydurma_kimlik  # noqa: E402

DB = {"F000001": ("F", "F000001"), "F000002": ("F", "F000002"), "F000003": ("F", "F000003"),
      "M00001": ("M", "M00001"), "489434": ("K", "489434"), "H0000010": ("H", "H0000010")}
SORU = {"soru_id": "X", "soru": "M00001 müşterisinin en son faturası hangisi?", "dogru_kimlikler": ["F000001"]}


def cagri(sonuc, hata=None):
    return {"arac": "fatura_ara", "argumanlar": {}, "hata": hata, "yanit": {"sonuc": sonuc}}


SONUC = [cagri({"faturalar": [{"fatura_id": "F000001", "kaynak_fatura_no": "489434", "musteri": "M00001"},
                              {"fatura_id": "F000002"}]})]


def test_adaylar_numaralari_bulur_tutar_ve_tarihi_almaz():
    metin = ("Çekler: CK-2026-0891, F 035473, #F035474, F-035475'in yanında T00039E4A ve 489434. "
             "Tutar 250000 TL, 1.234.567,89 TL; tarih 2026-08-12; TL 500.")
    assert sorted(adaylar(metin)) == sorted(["T00039E4A", "CK-2026-0891", "#F035474", "F-035475", "F 035473", "489434"])


def test_bicim_siniflari():
    assert [bicim(x) for x in ("F035473", "F-2026-089", "F 035473", "#F035473", "TA3B7C1D9", "489434",
                               "1, 2, 3")] == ["sistem", "ayracli", "ayracli", "ayracli", "karisik", "rakam", "rakam"]


def test_etiketler_araç_sonucuna_ve_veritabanina_gore():
    def et(metin, cagrilar=SONUC):
        return [(a["ham"], a["etiket"]) for a in etiketle(metin, SORU, cagrilar, DB)["anmalar"]]

    assert et("Son fatura F000001.") == [("F000001", "dogru")]
    assert et("Son fatura F000002.") == [("F000002", "yanlis_kayit")]          # sonuçta var, doğru değil
    assert et("Son fatura F000003.") == [("F000003", "desteksiz_gercek")]      # veritabanında var, sonuçta yok
    assert et("Son fatura F099999.") == [("F099999", "uydurma")]
    assert et("Son fatura F-000001.") == [("F-000001", "dogru")]               # ayraç farkı aynı numara
    assert et("M00001 müşterisinin faturası F000001.") == [("M00001", "sorudaki"), ("F000001", "dogru")]
    assert et("Son fatura F000001.", []) == [("F000001", "desteksiz_dogru")]   # doğru ama araç sonucunda yok
    assert et("Son fatura F000001.", [cagri({"faturalar": [{"fatura_id": "F000001"}]}, hata="yetki_yok")]) == \
        [("F000001", "desteksiz_dogru")]                                         # hatalı çağrı destek sayılmaz


def test_metnin_etiketi_en_kotu_anmadir():
    assert etiketle("F000001 ve F099999", SORU, SONUC, DB)["etiket"] == "uydurma"
    assert etiketle("F000001 ve F000002", SORU, SONUC, DB)["etiket"] == "yanlis_kayit"
    assert etiketle("Bulamadım.", SORU, SONUC, DB)["etiket"] == "numarasiz"


def test_el_dosyasi_aday_ekler_ve_cikarir():
    e = etiketle("Numara: KAYIT 77 ve F000002", SORU, SONUC, DB, {"ekle": ["KAYIT 77"], "cikar": ["F000002"]})
    assert [(a["ham"], a["etiket"]) for a in e["anmalar"]] == [("KAYIT 77", "uydurma")]


def taslak(metin, eski_gecerli=True, kimliksiz=(), sahipsiz=False):
    return {"metin": metin, "eski_gecerli": eski_gecerli, "gecerli": eski_gecerli and not kimliksiz and not sahipsiz,
            "kimliksiz": list(kimliksiz), "kaynaksiz": [] if eski_gecerli else ["1"], "sahipsiz": sahipsiz,
            "arac_cagrisi": 1, "kesildi": False}


def test_hangi_kural_durdurdu():
    k = {"cagrilar": SONUC}

    def d(t):
        return _taslak_okuma(t, SORU, k, DB, None)["durduran"]

    assert d(taslak("F099999", kimliksiz=["F099999"])) == "yalniz_yeni_kural"
    assert d(taslak("F-2026-099", eski_gecerli=False)) == "eski_kurallar"
    assert d(taslak("F099999", eski_gecerli=False, kimliksiz=["F099999"])) == "ikisi"
    assert d(taslak("TA3B7C1D9")) == "durdurulmadi"
    assert d(taslak("F000002")) is None              # yanlış kayıt desteksiz değil: kuralın işi değil
    assert d(taslak("F000001", kimliksiz=["F000001"])) is None  # yanlış bayrak: analizde ayrıca listelenir


def test_uydurma_kimlik_kontrolu_bicimleri_ve_bugunku_kural():
    bicimler = {}
    for i in range(60):
        b, numara = uydurma_kimlik(f"K-X-{i:02d}")
        bicimler.setdefault(b, numara)
    assert set(bicimler) == {"sistem", "tireli", "karisik"}
    assert [bicim(bicimler[b]) for b in ("sistem", "tireli", "karisik")] == ["sistem", "ayracli", "karisik"]
    cagrilar = [{"arac": "fatura_ara", "argumanlar": {}, "hata_mi": False,
                 "yanit": {"cagri_id": "C1", "arac": "fatura_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
                           "sonuc": {"faturalar": [{"fatura_id": "F035473"}]}}}]
    sonuc = {b: dogrula(f"Kayıt numarası: {n}.", "Numarası ne?", cagrilar, dt.date(2026, 9, 1))
             for b, n in bicimler.items()}
    assert sonuc["sistem"].kimliksiz and not sonuc["sistem"].kaynaksiz
    assert not sonuc["karisik"].kimliksiz and not sonuc["karisik"].kaynaksiz  # bilinen kör nokta


def test_donmus_kimlik_seti_butun():
    veri = json.loads((KOK / "reports" / "faz8b_kimlik_soru_seti.json").read_text(encoding="utf-8"))
    sorular = veri["sorular"]
    assert veri["ozet"] == f7.ozet(sorular)
    assert veri["sayilar"]["grup"] == {"D": 10, "U": 20, "Y": 6}
    for s in sorular:
        assert s["set"] == "kimlik" and s["dogru_kimlikler"]
        if s["grup"] == "D":
            a = s["alternatifler"][0]["anahtarlar"][0]
            assert s["bolum"] == "arac" and a["tur"] == "kimlik" and [a["deger"]] == s["dogru_kimlikler"]
        else:
            assert s["bolum"] == "yetkisiz" and s["tur"] == "kimlik_yok" and s["gizli"] == []
    assert veri["denetimler"]["sizinti"]["temiz"] and not veri["denetimler"]["ortusme"]["ihlal"]
