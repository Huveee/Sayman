"""Faz 8 eğitim verisi: sızıntı denetimi (bilerek sızıntı eklenince yakalıyor mu), öğretmen, tamamlama biçimi,
eğitim şablonlarının kuralları ve (varsa) üretilmiş verinin tutarlılığı."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import support.phase07_questions as f7
import support.phase08_training_questions as e8
from sayman.assistant.orchestrator import ModelCevabi
from sayman.training import leakage
from sayman.training.prompt import BITIS, KayitciModel, arac_cagrisi_metni, tamamlama
from sayman.training.teacher import CevapYok, Ogretmen, belge_cevabi

KOK = Path(__file__).resolve().parents[1]
VERI = KOK / "data" / "egitim" / "veri"


# ------------------------------------------------------------------ sızıntı denetimi


def _degerlendirme() -> leakage.Degerlendirme:
    return leakage.Degerlendirme(
        kaliplar=["{musteri} için toplam açık alacağımız ne kadar?", "Bugün {pb} alış kuru kaç?"],
        sorular=["Vadi Mavi Dekorasyon için toplam açık alacağımız ne kadar?", "Bugün USD alış kuru kaç?"],
        kimlikler={"M00227", "F010098"}, adlar={leakage.normal("Vadi Mavi Dekorasyon Ltd. Şti.")},
        madde_metinleri={"Vadesi 7 günü geçen alacak için müşteriye hatırlatma e-postası gönderilir."},
        kurlar={("USD", "48.1745")})


def _temiz_soru(**ek) -> dict:
    return {"soru_id": "E-1", "soru": "Koru Cam firmasının henüz kapanmamış kaç faturası bulunuyor?",
            "kalip": "{musteri} firmasının henüz kapanmamış kaç faturası bulunuyor?", **ek}


def test_temiz_egitim_sorusu_gecer():
    r = leakage.denetle([_temiz_soru(varliklar=["M09999"], adlar=["Koru Cam"])], _degerlendirme())
    assert r.temiz, r.ihlaller


@pytest.mark.parametrize("ek, tur", [
    ({"kalip": "{musteri} için toplam açık alacağımız ne kadar?"}, "kalip"),           # aynı iskelet
    ({"kalip": "{Musteri} için toplam açık alacağımız ne kadar acaba?"}, "kalip"),     # çok benzer iskelet
    ({"soru": "Vadi Mavi Dekorasyon için toplam açık alacağımız ne kadardır?"}, "soru"),
    ({"varliklar": ["F010098"]}, "kimlik"),
    ({"adlar": ["VADİ MAVİ DEKORASYON LTD ŞTİ"]}, "ad"),                               # büyük harf, noktasız
    ({"hedef_metin": "Vadesi 7 günü geçen alacak için müşteriye hatırlatma e-postası gönderilir."}, "madde"),
    ({"kur": ["USD", "48.1745"]}, "kur"),
])
def test_bilerek_eklenen_sizinti_yakalanir(ek, tur):
    r = leakage.denetle([_temiz_soru(**ek)], _degerlendirme())
    assert not r.temiz
    assert r.ihlaller[tur], r.ihlaller


def test_iskelet_degiskenleri_maskeler_ve_turkce_kucultur():
    assert leakage.iskelet("{Donem} İçin DSO kaç gün?") == "<v> için dso kaç gün"
    assert leakage.jaccard("a b c", "a b d") == pytest.approx(0.5)


def test_esikler_denetimden_once_konmus_degerler():
    assert leakage.ESIK_KALIP == 0.6 and leakage.ESIK_SORU == 0.8


# ------------------------------------------------------------------ tamamlama biçimi


def test_arac_cagrisi_tamamlamasi_xml_ve_bitis():
    t = tamamlama(ModelCevabi(arac_cagrilari=[("musteri_ozeti", {"musteri_adi": "Koru Cam"})]))
    assert t == ("<tool_call>\n<function=musteri_ozeti>\n<parameter=musteri_adi>\nKoru Cam\n</parameter>\n"
                 "</function>\n</tool_call>" + BITIS)


def test_argumansiz_ve_cok_cagrili_tamamlama():
    assert tamamlama(ModelCevabi(arac_cagrilari=[("nakit_pozisyonu", {})])) == \
        "<tool_call>\n<function=nakit_pozisyonu>\n</function>\n</tool_call>" + BITIS
    iki = arac_cagrisi_metni([("a", {"n": 3}), ("b", {})])
    assert iki == "<tool_call>\n<function=a>\n<parameter=n>\n3\n</parameter>\n</function>\n</tool_call>" \
                  "\n<tool_call>\n<function=b>\n</function>\n</tool_call>"


def test_metin_cevabi_tamamlamasi():
    assert tamamlama(ModelCevabi(icerik="  Toplam 5 TL.\n")) == "Toplam 5 TL." + BITIS


def test_kayitci_model_mesajlarin_o_anki_kopyasini_tutar():
    class Sahte:
        soru = None

        def __call__(self, m, a, z):
            return ModelCevabi(icerik="x")
    k = KayitciModel(Sahte())
    mesajlar = [{"role": "user", "content": "a"}]
    k(mesajlar, [], 1)
    mesajlar.append({"role": "assistant", "content": "b"})
    assert len(k.bosalt()[0]["mesajlar"]) == 1
    k.soru = {"x": 1}
    assert k.model.soru == {"x": 1}


# ------------------------------------------------------------------ öğretmen


def _mesajlar(soru="soru", sonuclar=()):
    m = [{"role": "system", "content": "s"}, {"role": "user", "content": soru}]
    if sonuclar:
        m.append({"role": "assistant", "content": "", "tool_calls": []})
        m += [{"role": "tool", "content": json.dumps(r, ensure_ascii=False)} for r in sonuclar]
    return m


def test_ogretmen_ilk_adim_rolden_bagimsiz():
    ogr = Ogretmen()
    cevaplar = []
    for rol in ("cfo", "satis_yurtici"):
        ogr.soru = {"soru_id": "E-1", "bolum": "yetkisiz", "rol": rol, "cagri": ["nakit_pozisyonu", {}]}
        cevaplar.append(ogr(_mesajlar(), [], 1).arac_cagrilari)
    assert cevaplar[0] == cevaplar[1] == [("nakit_pozisyonu", {})]


def test_ogretmen_arac_cevabi_ve_duzeltme_turunu_reddeder():
    ogr = Ogretmen()
    ogr.soru = {"soru_id": "E-2", "bolum": "arac", "cagri": ["x", {}], "cevap": "Toplam 5 TL."}
    assert ogr(_mesajlar(sonuclar=[{"arac": "x", "sonuc": {"t": "5"}}]), [], 1).icerik == "Toplam 5 TL."
    duzeltme = _mesajlar(sonuclar=[{"arac": "x", "sonuc": {}}]) + [{"role": "assistant", "content": "y"},
                                                                    {"role": "user", "content": "düzelt"}]
    with pytest.raises(CevapYok):
        ogr(duzeltme, [], 1)


def _belge_sorusu():
    return {"soru_id": "E-belge-1", "bolum": "belge", "dogru_parca": "SOZ-S001#m3", "deger": "90 gün",
            "anahtarlar": [{"tur": "sayi", "deger": "90"}], "konu_cumlesi": "Koru Cam sözleşmesine göre ödeme vadesi",
            "ikinci_sorgu": "Koru Cam ödeme vadesi", "cagri": ["belge_ara", {"sorgu": "q"}]}


def _belge_sonucu(*parcalar):
    return {"arac": "belge_ara", "sonuc": {"sonuclar": [{"parca_id": p, "metin": m} for p, m in parcalar]}}


def test_ogretmen_belge_dogru_madde_gelince_alintilar():
    ogr = Ogretmen()
    ogr.soru = _belge_sorusu()
    metin = "ŞİRKET, faturaları fatura tarihinden itibaren 90 gün içinde banka havalesiyle öder."
    c = ogr(_mesajlar(sonuclar=[_belge_sonucu(("SOZ-S002#m3", "başka"), ("SOZ-S001#m3", metin))]), [], 1)
    assert c.icerik.startswith("Koru Cam sözleşmesine göre ödeme vadesi: 90 gün. \"")
    assert c.icerik.endswith("[SOZ-S001#m3]") and "90 gün içinde" in c.icerik


def test_ogretmen_belge_gelmezse_bir_kez_daha_arar_sonra_vazgecer():
    ogr = Ogretmen()
    ogr.soru = _belge_sorusu()
    ilk = _mesajlar(sonuclar=[_belge_sonucu(("SOZ-S002#m3", "başka madde metni burada"))])
    assert ogr(ilk, [], 1).arac_cagrilari == [("belge_ara", {"sorgu": "Koru Cam ödeme vadesi"})]
    iki = _mesajlar(sonuclar=[_belge_sonucu(("SOZ-S002#m3", "a b c d")), _belge_sonucu(("SOZ-S009#m1", "e f g h"))])
    with pytest.raises(CevapYok):
        ogr(iki, [], 1)


def test_belge_cevabi_rakam_maddede_yoksa_ozet_yazmaz():
    s = {**_belge_sorusu(), "deger": "12 ay", "anahtarlar": [{"tur": "sayi", "deger": "12"}]}
    c = belge_cevabi(s, "Taahhüt süresi on iki aydır ve uzatılabilir.", "SOZ-S001#m3")
    assert "12" not in c and c.startswith("Koru Cam sözleşmesine göre ödeme vadesi maddede şöyle geçiyor:")


def test_ogretmen_baska_bolge_musterisini_bulamadi_der():
    ogr = Ogretmen()
    ogr.soru = {"soru_id": "E-3", "bolum": "yetkisiz", "tur": "baska_musteri", "cagri": ["musteri_ozeti", {}],
                "cevap": "Bulamadım."}
    c = ogr(_mesajlar(sonuclar=[{"arac": "musteri_ozeti", "sonuc": {"bulunamadi": True}}]), [], 1)
    assert c.icerik == "Bulamadım."
    with pytest.raises(CevapYok):  # müşteri bulunduysa (kapsam sızdıysa) eğitime alınmaz
        ogr(_mesajlar(sonuclar=[{"arac": "musteri_ozeti", "sonuc": {"acik_alacak_tl": "5"}}]), [], 1)


# ------------------------------------------------------------------ şablon kuralları


def test_haric_niyetler_egitim_sablonlarinda_yok_ve_faz7de_var():
    egitim = {sb.ad for sb in e8.SABLONLAR}
    faz7 = {sb.ad for sb in f7.SABLONLAR}
    assert not egitim & set(e8.HARIC_NIYETLER)
    assert set(e8.HARIC_NIYETLER) <= faz7


def test_egitim_gunleri_degerlendirme_gunu_degil():
    assert e8.DEGERLENDIRME_GUNU == dt.date(2026, 9, 1)
    assert e8.DEGERLENDIRME_GUNU not in e8.EGITIM_GUNLERI


def test_egitim_kaliplari_faz7_kaliplarindan_farkli():
    faz7 = {leakage.iskelet(k) for sb in f7.SABLONLAR for k in sb.kaliplar}
    for sb in e8.SABLONLAR:
        for k in sb.kaliplar:
            assert leakage.iskelet(k) not in faz7, k


def test_cevap_doldur_eksi_tutar_ve_eksik_anahtar():
    a = [{"ad": "net", "tur": "tutar", "deger": "-1234.5", "birim": "TL"}]
    assert e8.cevap_doldur("{n} haftalık tahminde ilk haftanın neti [net].", {"n": 3}, a) == \
        "3 haftalık tahminde ilk haftanın neti -1.234,50 TL."
    with pytest.raises(KeyError):
        e8.cevap_doldur("[yok] TL", {}, a)


def test_her_sablonun_cevaplari_anahtarlarini_kullanir():
    for sb in e8.SABLONLAR:
        for c in sb.cevaplar:
            assert "[" in c and "]" in c, sb.ad


# ------------------------------------------------------------------ üretilmiş veri (varsa)


def _veri_oku():
    if not (VERI / "egitim.jsonl").exists():
        pytest.skip("eğitim verisi henüz üretilmedi (scripts/phase08_training_data.py)")
    return {ad: [json.loads(x) for x in (VERI / f"{ad}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            for ad in ("egitim", "dogrulama")}


def test_ayni_istem_ayni_tamamlama():
    veri = _veri_oku()
    gorulen = {}
    for o in veri["egitim"] + veri["dogrulama"]:
        assert gorulen.setdefault(o["istem"], o["tamamlama"]) == o["tamamlama"], o["soru_id"]


def test_tamamlamalar_bitis_ile_biter_ve_istemler_asistan_basligiyla():
    for liste in _veri_oku().values():
        for o in liste:
            assert o["tamamlama"].endswith(BITIS) and o["tamamlama"].count(BITIS) == 1
            assert o["istem"].endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")


def test_egitim_ve_dogrulama_sorulari_ayri():
    veri = _veri_oku()
    assert not {o["soru_id"] for o in veri["egitim"]} & {o["soru_id"] for o in veri["dogrulama"]}
