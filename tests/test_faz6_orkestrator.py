"""Faz 6: orkestratör, senaryosu önceden yazılmış sahte bir modelle (gerçek model çalışmaz).

Araçlar gerçektir: Faz 3'ün elle hesaplanmış küçük şirketi üzerinde, gerçek kapıdan çağrılır (D = 31 Mart 2025).
Sahte model senaryosu bitince bir kez daha çağrılırsa hata verir; böylece "modele bir daha gidilmedi" de sınanır.

Her testte değişmez kural denetlenir: kullanıcıya gösterilen metin ya doğrulanmış taslaktır ya da sabit bir metin.
"""

import copy
import datetime as dt
import json
from decimal import Decimal

import pytest
from test_faz2_ambar import D, _elle_tablolar
from test_faz6_belge_araci import PARCALAR

from sayman.ambar.kurulum import ambar_kur
from sayman.araclar.kayit import DenetimKaydi
from sayman.araclar.kimlik import kart_uret
from sayman.araclar.sunucu import SaymanSunucusu
from sayman.araclar.yetki import yetki_oku
from sayman.asistan.orkestrator import (METINLER, SISTEM_ISTEMI, Asistan, Ayar, ModelCevabi, ModelZamanAsimi,
                                        arac_tanimlari)
from sayman.belge.arama import Arayici
from sayman.veri.cikti import csv_yaz
from sayman.veri.saat import Saat

ANAHTAR = bytes(range(32))
ZAMAN = 1_750_000_000.0
CFO, YURTICI = "selin.aydin", "murat.kaya"
NAKIT = "Toplam nakit 12.396,40 TL."  # elle veri: CFO, 31 Mart 2025


@pytest.fixture(scope="module")
def sunucu(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("faz6_ork")
    csv_yaz(_elle_tablolar(), klasor / "csv")
    ambar_kur(klasor / "csv", klasor / "sayman.duckdb").close()
    s = SaymanSunucusu(ambar=klasor / "sayman.duckdb", saat=lambda: Saat(D, dt.date(2025, 6, 30)), anahtar=ANAHTAR,
                       yetki=yetki_oku(), kayit=DenetimKaydi(), kapsam_klasoru=klasor / "kapsam",
                       zaman=lambda: ZAMAN, belge=Arayici(PARCALAR), belge_yontemi="bm25")
    yield s
    s.kapat()


def kart(kullanici):
    return kart_uret(kullanici, ANAHTAR, 3600, ZAMAN)


class SahteModel:
    """Senaryo: her model çağrısı için bir ModelCevabi, mesajlardan cevap üreten bir işlev ya da fırlatılacak hata."""

    def __init__(self, *senaryo, saat=None, gecikme=0.0):
        self.senaryo, self.saat, self.gecikme = list(senaryo), saat, gecikme
        self.cagrilar: list[list[dict]] = []
        self.zaman_asimlari: list[float] = []

    def __call__(self, mesajlar, araclar, zaman_asimi):
        assert araclar == arac_tanimlari()
        if not self.senaryo:
            raise AssertionError("senaryo bitti ama model bir daha çağrıldı")
        self.cagrilar.append(copy.deepcopy(mesajlar))
        self.zaman_asimlari.append(zaman_asimi)
        if self.saat is not None:
            self.saat.t += self.gecikme
        adim = self.senaryo.pop(0)
        if isinstance(adim, Exception):
            raise adim
        return adim(mesajlar) if callable(adim) else adim


class SahteSaat:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def arac(ad, **args):
    return ModelCevabi(arac_cagrilari=[(ad, args)], istem_token=100, uretim_token=10)


def cevap(metin, **ek):
    return ModelCevabi(icerik=metin, **ek)


def tr(tutar: str) -> str:
    """Araç biçimi "6745.00" -> Türkçe "6.745,00"."""
    d = Decimal(tutar)
    tam, kesir = f"{abs(d):.2f}".split(".")
    return ("-" if d < 0 else "") + f"{int(tam):,}".replace(",", ".") + "," + kesir


def son_arac_sonucu(mesajlar):
    return json.loads(next(m for m in reversed(mesajlar) if m["role"] == "tool")["content"])


def calistir(sunucu, model, kullanici=CFO, soru="Bankalarda ne kadar nakit var?", ayar=Ayar(), saat=None, k=None):
    tur_kaydi = DenetimKaydi()
    asistan = Asistan(sunucu, model, ayar, **({"saat": saat} if saat else {}), kayit=tur_kaydi)
    once = len(sunucu.kayit.satirlar)
    tur = asistan.sor(soru, kart(kullanici) if k is None else k)
    _degismez(tur)
    [tur_satiri] = tur_kaydi.satirlar
    assert tur_satiri["durum"] == tur.durum and tur_satiri["tur_id"] == tur.tur_id
    return tur, sunucu.kayit.satirlar[once:], tur_satiri


def _degismez(tur):
    if tur.durum == "tamam":
        assert tur.dogrulamalar[-1].gecerli and tur.cevap == tur.taslaklar[-1].strip()
    else:
        assert tur.cevap in METINLER.values()
        for taslak in tur.taslaklar:
            assert not taslak.strip() or taslak.strip() not in tur.cevap


# ---------------------------------------------------------------- tamam


def test_tamam_arac_sonra_dogrulanmis_cevap(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), cevap(NAKIT))
    tur, arac_satirlari, tur_satiri = calistir(sunucu, model)
    assert (tur.durum, tur.cevap, tur.model_cagrisi) == ("tamam", NAKIT, 2)
    assert [(r["arac"], r["kullanici"], r["sonuc"]) for r in arac_satirlari] == [("nakit_pozisyonu", CFO, "tamam")]
    assert tur_satiri["cagri_idleri"] == [arac_satirlari[0]["cagri_id"]] and tur_satiri["kullanici"] == CFO
    ikinci = model.cagrilar[1]
    assert [m["role"] for m in ikinci] == ["system", "user", "assistant", "tool"]
    assert D.isoformat() in ikinci[0]["content"]
    assert son_arac_sonucu(ikinci)["sonuc"]["toplam_tl"] == "12396.40"


def test_kimlik_karti_mesajlara_girmez(sunucu):
    k = kart(CFO)
    model = SahteModel(arac("nakit_pozisyonu"), cevap(NAKIT))
    calistir(sunucu, model, k=k)
    metin = json.dumps(model.cagrilar, ensure_ascii=False)
    assert k not in metin and k.split(".")[1] not in metin


def test_arac_hatasi_modele_geri_verilir(sunucu):
    """Şema hatası "ret" değildir: model hatayı görür ve düzeltir."""
    model = SahteModel(arac("nakit_pozisyonu", tarih="dün"), arac("nakit_pozisyonu"), cevap(NAKIT))
    tur, arac_satirlari, _ = calistir(sunucu, model)
    assert tur.durum == "tamam" and tur.model_cagrisi == 3
    assert [r["sonuc"] for r in arac_satirlari] == ["reddedildi", "tamam"]
    assert son_arac_sonucu(model.cagrilar[1])["hata"] == "gecersiz_arguman"


def test_belge_alintisi_dogrulanir(sunucu):
    def alintila(mesajlar):
        [p] = [p for p in son_arac_sonucu(mesajlar)["sonuc"]["sonuclar"] if p["parca_id"] == "SOZ-M1#m3"]
        return cevap(f'Alfa Ltd. 30 gün içinde öder: "faturayı 30 gün içinde öder" [{p["parca_id"]}]')

    model = SahteModel(arac("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"), alintila)
    tur, _, _ = calistir(sunucu, model, soru="Alfa Ltd. faturayı kaç günde öder?")
    assert tur.durum == "tamam" and [a.neden for a in tur.dogrulamalar[-1].alintilar] == ["tamam"]


# ---------------------------------------------------------------- ret


def test_ret_yetkisiz_arac_modele_bir_daha_gidilmez(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"))  # ikinci çağrı olursa SahteModel hata verir
    tur, arac_satirlari, tur_satiri = calistir(sunucu, model, kullanici=YURTICI)
    assert (tur.durum, tur.cevap, tur.model_cagrisi) == ("ret", "Bu bilgiyi görme yetkiniz yok.", 1)
    assert [(r["arac"], r["sonuc"], r["neden"]) for r in arac_satirlari] == \
        [("nakit_pozisyonu", "reddedildi", "yetki_yok")]
    assert tur_satiri["neden"] == "yetki_yok"


def test_ret_kimlik_gecersizse_model_hic_cagrilmaz(sunucu):
    model = SahteModel()
    tur, arac_satirlari, tur_satiri = calistir(sunucu, model, k="bozuk.kart")
    assert (tur.durum, tur.cevap, tur.model_cagrisi) == ("ret", "Kimlik doğrulanamadı.", 0)
    assert arac_satirlari == [] and tur_satiri["neden"] == "imza_gecersiz" and tur_satiri["kullanici"] is None


def test_ret_kart_tur_bitmeden_dolacaksa_baslamaz(sunucu):
    kisa = kart_uret(CFO, ANAHTAR, 200, ZAMAN)  # 200 sn geçerli; tur süresi 300 sn
    tur, _, tur_satiri = calistir(sunucu, SahteModel(), k=kisa)
    assert tur.durum == "ret" and tur_satiri["neden"] == "kart_suresi_dolmus"


def test_karisik_adimda_bir_ret_donguyu_bitirmez(sunucu):
    def yanitla(mesajlar):
        return cevap(f"Açık alacak toplamı {tr(son_arac_sonucu(mesajlar)['sonuc']['toplam_tl'])} TL.")

    model = SahteModel(ModelCevabi(arac_cagrilari=[("nakit_pozisyonu", {}), ("alacak_yaslandirma", {})]), yanitla)
    tur, arac_satirlari, _ = calistir(sunucu, model, kullanici=YURTICI, soru="Nakit ve alacak durumu?")
    assert tur.durum == "tamam"
    assert [(r["arac"], r["sonuc"]) for r in arac_satirlari] == \
        [("nakit_pozisyonu", "reddedildi"), ("alacak_yaslandirma", "tamam")]


# ---------------------------------------------------------------- düzeltme ve "cevap üretemedim"


def test_duzeltme_sonrasi_dogru_cevap(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), cevap("Toplam nakit 12.936,40 TL."), cevap(NAKIT))
    tur, _, _ = calistir(sunucu, model)
    assert (tur.durum, tur.cevap, len(tur.taslaklar)) == ("tamam", NAKIT, 2)
    duzeltme = model.cagrilar[2][-1]
    assert duzeltme["role"] == "user" and "12.936,40" in duzeltme["content"]
    assert model.cagrilar[2][-2] == {"role": "assistant", "content": "Toplam nakit 12.936,40 TL."}


def test_uretemedim_ikinci_taslak_da_yanlis(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), cevap("Toplam nakit 12.936,40 TL."),
                       cevap("Toplam nakit yaklaşık 13 milyon TL."))
    tur, _, tur_satiri = calistir(sunucu, model)
    assert (tur.durum, tur.cevap) == ("uretemedim", METINLER["uretemedim"])
    assert "cevap üretemedim" in tur.cevap and "12.936" not in tur.cevap and "13 milyon" not in tur.cevap
    assert tur_satiri["kaynaksiz"] == ["13 milyon"] and tur_satiri["taslak_sayisi"] == 2


def test_uydurma_alinti_duzeltilmezse_uretemedim(sunucu):
    model = SahteModel(arac("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"),
                       cevap('Vade 30 gün: "alıcı faturayı en geç 45 gün içinde öder" [SOZ-M1#m3]'),
                       cevap('Vade 30 gün: "gecikmede aylık faiz uygulanır" [SOZ-M1#m9]'))
    tur, _, tur_satiri = calistir(sunucu, model, soru="Alfa Ltd. vadesi?")
    assert tur.durum == "uretemedim"
    assert tur_satiri["gecersiz_alinti"] == ["bilinmeyen_parca"]
    assert "parçanın metninde aynen geçmiyor" in model.cagrilar[2][-1]["content"]


def test_kesik_cevap_dogrulanmamis_sayilir(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), cevap("Toplam nakit 12.396,40 TL; ayrıca", kesildi=True),
                       cevap(NAKIT))
    tur, _, _ = calistir(sunucu, model)
    assert tur.durum == "tamam" and tur.cevap == NAKIT
    assert "yarıda kesildi" in model.cagrilar[2][-1]["content"]


def test_bos_cevap_iki_kez_uretemedim(sunucu):
    tur, _, _ = calistir(sunucu, SahteModel(cevap(""), cevap("  ")))
    assert tur.durum == "uretemedim"


# ---------------------------------------------------------------- sınırlar


def test_adim_siniri(sunucu):
    model = SahteModel(arac("doviz_kuru", para_birimi="USD"), arac("doviz_kuru", para_birimi="EUR"),
                       arac("doviz_kuru", para_birimi="GBP"))
    tur, arac_satirlari, tur_satiri = calistir(sunucu, model, ayar=Ayar(en_cok_adim=3))
    assert (tur.durum, tur.cevap, tur.model_cagrisi) == ("adim_siniri", METINLER["adim_siniri"], 3)
    assert len(arac_satirlari) == 3 and tur_satiri["neden"] == "model_cagrisi"


def test_duzeltme_icin_adim_kalmadiysa_adim_siniri(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), cevap("Toplam nakit 12.936,40 TL."))
    tur, _, _ = calistir(sunucu, model, ayar=Ayar(en_cok_adim=2))
    assert tur.durum == "adim_siniri" and "12.936" not in tur.cevap


def test_arac_cagrisi_siniri_asilinca_hicbiri_calismaz(sunucu):
    uc = ModelCevabi(arac_cagrilari=[("doviz_kuru", {"para_birimi": p}) for p in ("USD", "EUR", "GBP")])
    tur, arac_satirlari, tur_satiri = calistir(sunucu, SahteModel(uc), ayar=Ayar(en_cok_cagri=2))
    assert tur.durum == "adim_siniri" and tur_satiri["neden"] == "arac_cagrisi" and arac_satirlari == []


def test_sure_siniri_model_cagrisindan_sonra(sunucu):
    saat = SahteSaat()
    model = SahteModel(arac("nakit_pozisyonu"), cevap(NAKIT), saat=saat, gecikme=170.0)
    tur, _, _ = calistir(sunucu, model, saat=saat)
    assert (tur.durum, tur.cevap, tur.model_cagrisi) == ("sure_siniri", METINLER["sure_siniri"], 2)
    assert model.zaman_asimlari == [pytest.approx(300.0), pytest.approx(130.0)]  # kalan süre zaman aşımı olur


def test_sure_siniri_model_zaman_asimi(sunucu):
    model = SahteModel(arac("nakit_pozisyonu"), ModelZamanAsimi("okuma zaman aşımı"))
    tur, _, _ = calistir(sunucu, model)
    assert tur.durum == "sure_siniri" and tur.cevap == METINLER["sure_siniri"]


def test_sure_siniri_cagri_oncesi(sunucu):
    saat = SahteSaat()
    model = SahteModel(arac("nakit_pozisyonu"), saat=saat, gecikme=0.0)

    def kapi_yavas(ad, arg, k, _asil=sunucu.kapi):
        saat.t += 500  # araç uzun sürdü
        return _asil(ad, arg, k)

    sunucu.kapi = kapi_yavas
    try:
        tur, _, _ = calistir(sunucu, model, saat=saat)
    finally:
        del sunucu.kapi
    assert tur.durum == "sure_siniri" and tur.model_cagrisi == 1


def test_model_hatasi(sunucu):
    tur, _, tur_satiri = calistir(sunucu, SahteModel(ConnectionError("Ollama kapalı")))
    assert (tur.durum, tur.cevap) == ("model_hatasi", METINLER["model_hatasi"])
    assert tur_satiri["neden"].startswith("ConnectionError")


def test_baglam_siniri_buyuk_arac_sonucundan_sonra(sunucu):
    ilk = [{"role": "system", "content": SISTEM_ISTEMI.format(bugun=D.isoformat())},
           {"role": "user", "content": "Alacaklar?"}]
    sinir = Asistan(sunucu, None, Ayar(arac_listesi_token=0))._token_tahmini(ilk) + 50
    model = SahteModel(ModelCevabi(arac_cagrilari=[("alacak_yaslandirma", {})]))  # istem token sayısı bildirmiyor
    tur, arac_satirlari, _ = calistir(sunucu, model, soru="Alacaklar?",
                                      ayar=Ayar(arac_listesi_token=0, cevap_payi=0, baglam_token=sinir))
    assert (tur.durum, tur.model_cagrisi) == ("baglam_siniri", 1) and len(arac_satirlari) == 1


def test_baglam_tahmini_modelin_bildirdigi_sayinin_ustune_yalnizca_yeni_mesajlari_ekler(sunucu):
    """Duman testinde (9. soru) bütün mesajların karakter tahmini gerçek istemi ~2.000 token fazla saydı. Model
    istem token sayısını bildirdiyse yalnızca sonradan eklenen mesajlar tahmin edilir."""
    asistan = Asistan(sunucu, None)
    mesajlar = [{"role": "system", "content": "x" * 1700}, {"role": "user", "content": "y" * 170}]
    yeni = {"role": "tool", "tool_name": "nakit_pozisyonu", "content": "z" * 1700}
    tam = asistan._token_tahmini(mesajlar + [yeni])
    artimli = asistan._token_tahmini(mesajlar + [yeni], bilinen=(3000, 2))
    assert artimli == 3000 + int(len(json.dumps(yeni)) / 1.7)
    assert tam == 2400 + int(sum(len(json.dumps(m)) for m in mesajlar + [yeni]) / 1.7)

    # Bildirilen sayı pencereye yakınsa yeni araç sonucu sığmaz: model bir daha çağrılmaz.
    model = SahteModel(ModelCevabi(arac_cagrilari=[("alacak_yaslandirma", {})], istem_token=7600, uretim_token=20))
    tur, _, _ = calistir(sunucu, model, soru="Alacaklar?")
    assert (tur.durum, tur.model_cagrisi) == ("baglam_siniri", 1)


def test_baglam_siniri_modelin_bildirdigi_istem_tokenindan(sunucu):
    """Karakter tahmini tutmasa bile model istemin pencereyi doldurduğunu bildirirse cevap kullanılmaz."""
    model = SahteModel(arac("nakit_pozisyonu"), cevap(NAKIT, istem_token=7900))
    tur, _, tur_satiri = calistir(sunucu, model)
    assert (tur.durum, tur_satiri["neden"], tur.taslaklar) == ("baglam_siniri", "istem_token", [])


def test_varsayilan_sure_faz0_hizindan():
    assert Ayar().sure_s == 300.0


def test_en_uzun_belge_sonucu_baglam_tahminine_sigar(sunucu):
    """Ölçülen en uzun belge sonucu (3 madde × 2.500 karakter = 8.224 karakter, gerçekte 2.580 token) ilk model
    çağrısından sonra (bildirilen ~2.776 token) pencereye sığar; JSON oranıyla (1,7) tahmin edilseydi sığmazdı."""
    belge = {"role": "tool", "tool_name": "belge_ara", "content": "ç" * 8224}
    cagri = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "belge_ara",
                                                                             "arguments": {"sorgu": "defter"}}}]}
    mesajlar = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}, cagri, belge]
    a = Ayar()
    sinir = a.baglam_token - a.cevap_payi
    assert Asistan(sunucu, None)._token_tahmini(mesajlar, bilinen=(2776, 2)) < sinir
    json_gibi = Asistan(sunucu, None, Ayar(belge_karakter_token=a.karakter_token))
    assert json_gibi._token_tahmini(mesajlar, bilinen=(2776, 2)) > sinir

def test_belge_cevabinda_alinti_yoksa_duzeltme_istenir(sunucu):
    """Faz 7 kararı: belge araması sonuç döndürdüyse cevapta en az bir geçerli alıntı olmalı."""
    model = SahteModel(arac("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"),
                       cevap("Alfa Ltd. faturayı 30 gün içinde öder."),
                       cevap('Alfa Ltd. 30 gün içinde öder: "faturayı 30 gün içinde öder" [SOZ-M1#m3]'))
    tur, _, tur_satiri = calistir(sunucu, model, soru="Alfa Ltd. faturayı kaç günde öder?")
    assert tur.durum == "tamam" and len(tur.taslaklar) == 2
    assert tur.dogrulamalar[0].alinti_eksik and not tur.dogrulamalar[0].gecerli
    assert "belge_ara kullandın ama cevapta geçerli bir alıntı yok" in model.cagrilar[2][-1]["content"]


def test_sistem_istemi_kisa_cevap_ve_alinti_kurali():
    assert "en çok 120 kelime" in SISTEM_ISTEMI and "en az bir alıntı olmalı" in SISTEM_ISTEMI
