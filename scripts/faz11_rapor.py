"""Faz 11 raporu: bütün metrik tablolarını reports/ altındaki ölçüm dosyalarından tek komutla yeniden üretir.

Hangi tablonun hangi dosyadan ve hangi anahtardan okunduğu aşağıdaki MANIFEST'te açıkça yazılıdır. "En son koşu"
dosya değişiklik zamanına göre değil, bu listeye göre belirlenir. Bir fazın birden çok rapor sürümü varsa README'nin
nihai sonuç diye sunduğu dosya seçilmiştir (nedeni ilgili bölümün üstündeki yorumda). Hiçbir ölçüm yeniden
çalıştırılmaz; betik yalnızca JSON dosyalarını okur.

Bir manifest dosyası ya da anahtarı eksikse hangisinin eksik olduğu yazılır ve çıkış kodu 0 olmaz; varsayılan değer
yoktur. Tek istisna "sayac" biçimidir: durum sayaçlarında (ör. durumlar.baglam_siniri) hiç görülmeyen durum anahtarı
yoktur; bu biçim üst sözlüğün var olmasını ister, yalnızca son anahtar yoksa 0 sayar. Çıktıda saat damgası yoktur:
aynı girdi, birebir aynı çıktıyı verir. Tarihler yalnızca ölçüm dosyalarının kendi tarihleridir.

Yol söz dizimi: "a.b.c" sözlük anahtarlarını izler; "liste[0]" dizinle, "liste[ad=x]" alan eşleşmesiyle, "sozluk[a.b]"
noktalı anahtarla, sondaki "#" uzunlukla okur.

Çıktı: reports/faz11_rapor.md ve reports/faz11_rapor.json

    .venv\\Scripts\\python -I scripts\\faz11_rapor.py
    .venv\\Scripts\\python -I scripts\\faz11_rapor.py --rapor-klasoru BASKA_KLASOR --cikti BASKA\\rapor.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]

# --- Dosya adları --------------------------------------------------------------------------------------------------
F0, F2, F3, F4, F5, F5K = "faz0_hiz.json", "faz2_metrikler.json", "faz3_araclar.json", "faz4_izleme.json", "faz5_arama.json", "faz5_kaynak.json"
F6I, F6, F6S = "faz6_duman_ilk.json", "faz6_duman.json", "faz6_duman_secili.json"
F7G, F7S = "faz7_olcum_gelistirme.json", "faz7_olcum_saklanan.json"
F8T, F8A, F8K = "faz8_temel_kendi_olcum_saklanan.json", "faz8_ayarli_olcum_saklanan.json", "faz8_karsilastirma_egitim_saklanan.json"
F8Z = "faz8_sizinti.json"
F8U = {"tekrar": "faz8_unutma_tekrar.json", "donusturme": "faz8_unutma_donusturme.json",
       "ince": "faz8_unutma.json", "kutuphane": "faz8_unutma_kutuphane_ayarli.json"}
F8BA, F8BY, F8BK, F8BS = "faz8b_analiz.json", "faz8b_ayarli_olcum_yeni.json", "faz8b_kimlik_analiz.json", "faz8b_soru_seti.json"
F9, F9B = "faz9_karsilastirma.json", "faz9_boyut.json"
FK = "kor_nokta_olcum.json"

# --- Manifest yardımcıları: hücre = (dosya, yol veya yol listesi, biçim); None = boş hücre -------------------------
KOLLAR = ("el", "otomatik", "duzeltme")


def hd(dosya, ad, alan, bicim):
    """Ölçüm raporundaki 'hedefler' listesinden bir hedefin alanı."""
    return (dosya, f"hedefler[ad={ad}].{alan}", bicim)


def tablo(id_, baslik, kolonlar, satirlar, not_=""):
    return {"id": id_, "baslik": baslik, "kolonlar": kolonlar, "satirlar": satirlar, "not": not_}


# Faz 7/8 hedef satırları: (etiket, hedef adı, değer biçimi)
HEDEFLER = [("Yetkisiz sızıntı (zorunlu)", "yetkisiz_sizinti", "sayi"), ("Araç seçimi", "arac_secimi", "oran"),
            ("Rakam doğruluğu", "rakam_dogrulugu", "oran"), ("Doğrulanmış yanlış sayı", "dogrulanmis_yanlis_sayi", "oran"),
            ("Belge isabeti", "belge_isabet", "oran"), ("Belge cevabı", "belge_cevap", "oran"),
            ("Belge alıntısı", "belge_alinti", "oran"), ("Süre medyanı (sn)", "sure_medyan", "sn"),
            ("Süre p90 (sn)", "sure_p90", "sn")]
KARSILASTIRMA = [("Araç seçimi (araç soruları)", "arac.metrikler.arac_secimi"), ("Parametreler (araç)", "arac.metrikler.parametre"),
                 ("Rakam doğruluğu (araç)", "arac.metrikler.cevap_dogrulugu"),
                 ("Doğrulanmış yanlış sayı (araç)", "arac.metrikler.dogrulanmis_yanlis_sayi"),
                 ("Doğru madde bulundu (belge)", "belge.metrikler.isabet"), ("Belge cevabı", "belge.metrikler.cevap_dogrulugu"),
                 ("Geçerli alıntı (belge)", "belge.metrikler.alinti_gecerli"), ("Yetkisiz denemede ret", "yetkisiz.metrikler.reddetti")]

MANIFEST = [
    # ---- Faz 0: tek dosya, tek sürüm -----------------------------------------------------------------------------
    ("Faz 0 · Model hız ölçümü", [
        tablo("faz0_hiz", "İki aday modelin hızı (bu bilgisayar, ekran kartsız)", ["Ölçüt", "Aday 1", "Aday 2"], [
            [e] + [(F0, f"adaylar[{i}].{k}", b) for i in (0, 1)] for e, k, b in [
                ("Model", "model", "metin"), ("Boyut (GB)", "boyut_gb", "iki"), ("Bellekteki boyut (GB)", "bellekteki_boyut_gb", "iki"),
                ("Soğuk tur (sn)", "soguk_tur_s", "sn"), ("İstem işleme (token/sn)", "istem_isleme_tps", "iki"),
                ("Üretim (token/sn)", "uretim_tps", "iki"), ("Ortanca tur (sn)", "ortanca_tur_s", "sn"),
                ("Geçerli araç çağrısı", "gecerli_sayisi", "sayi"), ("Kural sağlandı", "kural_saglandi", "evet_hayir")]],
              "Kural: ön bellek ısındıktan sonra her araç çağırma turu eşik süreyi aşmayacak, geçerli çağrı sayısı yeterli olacak."),
        tablo("faz0_secim", "Seçim", ["Ölçüt", "Değer"], [
            ["Seçilen model", (F0, "secilen_model", "metin")], ["Tur süresi eşiği (sn)", (F0, "kural.sure_esigi_s", "sayi")],
            ["Gereken geçerli çağrı", (F0, "kural.gereken_gecerli", "sayi")]]),
    ]),
    # ---- Faz 2: ambarın "şimdi" (simülasyon günü) metrik özeti; bağımsız pandas doğrulaması testlerdedir -----------------
    ("Faz 2 · Veri ambarı ve metrikler", [
        tablo("faz2_metrikler", "Ambarın hesapladığı metrikler", ["Metrik", "Değer"], [
            ["Gün", (F2, "tarih", "metin")], ["Nakit pozisyonu (TL)", (F2, "nakit_pozisyonu.toplam_try_kurus", "kurus_tl")],
            ["Alacak yaşlandırma toplamı (TL)", (F2, "yaslandirma_toplam.toplam_kurus", "kurus_tl")],
            ["Yaşlandırmadaki müşteri sayısı", (F2, "yaslandirma_musteri_sayisi", "sayi")],
            ["DSO (gün)", (F2, "dso.dso_gun", "iki")], ["DSO, 2025 yıl sonu (gün)", (F2, "dso_2025_yil_sonu.dso_gun", "iki")],
            ["Geciken fatura sayısı", (F2, "geciken_alacaklar.fatura_sayisi", "sayi")],
            ["Geciken alacak (TL)", (F2, "geciken_alacaklar.toplam_try_kurus", "kurus_tl")],
            ["Yaklaşan ödemelerin faturası", (F2, "yaklasan_odemeler.fatura_sayisi", "sayi")],
            ["Yaklaşan ödemeler (TL)", (F2, "yaklasan_odemeler.toplam_kurus", "kurus_tl")],
            ["Kur riski (TL)", (F2, "kur_riski.toplam_try_kurus", "kurus_tl")],
            ["Nakit akış tahmini, hafta sayısı", (F2, "nakit_akis_tahmini.hafta_sayisi", "sayi")],
            ["Nakit akış tahmini, açılış (TL)", (F2, "nakit_akis_tahmini.acilis_kurus", "kurus_tl")]],
              "Bu değerler ambarın çıktısıdır; her metriğin bağımsız pandas hesaplamasıyla karşılaştırması testlerdedir."),
    ]),
    # ---- Faz 3 ---------------------------------------------------------------------------------------------------
    ("Faz 3 · Araçlar ve yetki", [
        tablo("faz3_yetki", "Yetkisiz denemeler ve denetim kaydı", ["Ölçüt", "Değer"], [
            ["Araç sayısı", (F3, "araclar#", "sayi")], ["Yetkisiz deneme", (F3, "denemeler.toplam", "sayi")],
            ["Beklenen sonucu veren deneme", (F3, "denemeler.sorunsuz", "sayi")], ["Sızıntı", (F3, "denemeler.sizinti", "sayi")],
            ["Denetim kaydı satırı", (F3, "denetim_kaydi.satir", "sayi")], ["Zincir hatası", (F3, "denetim_kaydi.zincir_hatalari#", "sayi")],
            ["Araç listesi (token)", (F3, "token_olcumu.arac_listesi", "sayi")], ["En büyük cevap (token)", (F3, "token_olcumu.en_buyuk_cevap", "sayi")],
            ["Kapıda en yavaş çağrı (ms)", (F3, "sureler.kapida_en_yavas_ms", "sn")]]),
    ]),
    # ---- Faz 4: ayar tohumu 42, ayrılmış tohumlar 43 ve 44 -------------------------------------------------------------
    ("Faz 4 · İzleme ve uyarılar", [
        tablo("faz4_izleme", "Gömülü olayları yakalama ve olaysız veride yanlış alarm", [
            "Tohum", "Ayrılmış mı", "Olay", "Yakalanan", "Geri çağırma", "Aylık yanlış alarm"],
              [[f"Tohum {t}", (F4, f"setler.tohum{t}.ayrilmis", "evet_hayir"), (F4, f"setler.tohum{t}.olayli.puan.olay", "sayi"),
                (F4, f"setler.tohum{t}.olayli.puan.yakalanan", "sayi"), (F4, f"setler.tohum{t}.olayli.puan.geri_cagirma", "oran"),
                (F4, f"setler.tohum{t}.temiz.yanlis_alarm.aylik", "oran")] for t in (42, 43, 44)]
              + [["Kabul eşiği", None, None, None, (F4, "kabul.geri_cagirma_en_az", "sayi"), (F4, "kabul.aylik_yanlis_alarm_en_cok", "sayi")]],
              "Olayları ve kuralları aynı kişi yazdı; yüksek yakalama oranı gerçek dünyadaki başarıyı göstermez."),
    ]),
    # ---- Faz 5: README'deki 4 yöntem tablosu = faz5_arama.json; bellek ve süre = faz5_kaynak.json ---------------------
    ("Faz 5 · Belge arama", [
        tablo("faz5_arama", "Arama yöntemleri (ilk 5 sonuçta doğru madde)", [
            "Yöntem", "Ayar seti, ilk 5", "Ayrılmış set, ilk 5", "Ayrılmış, ilk sırada", "Ayrılmış soru", "Ayrılmış, kaçan"],
              [[e, (F5, f"yontemler.{y}.ayar.isabet@5", "oran"), (F5, f"yontemler.{y}.ayrilmis.isabet@5", "oran"),
                (F5, f"yontemler.{y}.ayrilmis.isabet@1", "oran"), (F5, f"yontemler.{y}.ayrilmis.soru", "sayi"),
                (F5, f"yontemler.{y}.ayrilmis.kacan", "sayi")] for e, y in [
                  ("Kelime araması (BM25)", "bm25"), ("Anlam araması (bge-m3)", "anlam"), ("Kelime + anlam", "karma"),
                  ("Kelime + anlam + sıralayıcı (kabul yöntemi)", "karma_sirala")]]
              + [["Kabul ölçütü sağlandı", None, (F5, "kabul", "evet_hayir"), None, None, None]]),
        tablo("faz5_kaynak", "Bellek ve süre (sohbet modeli, anlam modeli ve sıralayıcı birlikte)", ["Ölçüt", "Değer"], [
            ["Toplam bellek (GB)", (F5K, "toplam_bellek_gb", "sn")], ["Üçü yüklüyken boş bellek (GB)", (F5K, "bos_bellek_gb.hepsi_yuklu", "iki")],
            ["Sıralayıcı süreci, arama sonrası (GB)", (F5K, "surec_bellek_gb.arama_sonrasi", "iki")],
            ["Sıralayıcılı arama, soru başına medyan (sn)", (F5K, "arama_soru_basina_s.medyan", "sn")],
            ["Sıralayıcılı arama, en çok (sn)", (F5K, "arama_soru_basina_s.en_cok", "sn")],
            ["Sohbet modeli bellekten atıldı mı", (F5K, "sohbet_modeli_atildi_mi", "evet_hayir")]]),
    ]),
    # ---- Faz 6: ikinci çalıştırma (faz6_duman.json) README'nin ana sonucu; ilk ve seçili çalıştırma yanında ----------------
    ("Faz 6 · Doğrulayıcı ve orkestratör (duman testi)", [
        tablo("faz6_durumlar", "Duman testinin bitiş durumları", ["Çalıştırma", "tamam", "ret", "uretemedim", "sure_siniri", "baglam_siniri"], [
            [e] + [(d, f"ozet.durumlar.{k}", "sayac") for k in ("tamam", "ret", "uretemedim", "sure_siniri", "baglam_siniri")]
            for e, d in [("İlk çalıştırma", F6I), ("İkinci çalıştırma (doğrulayıcı düzeltmelerinden sonra)", F6), ("Seçili sorular, yeniden", F6S)]],
              "Bu bir kalite ölçümü değildir; sorular azdır ve düzeltmeler aynı sorularla yapıldı."),
        tablo("faz6_olcum", "İkinci çalıştırmanın ölçümleri", ["Ölçüt", "Değer"], [
            ["Soru", (F6, "ozet.soru", "sayi")], ["İlk taslağı reddedilen soru", (F6, "ozet.ilk_taslagi_reddedilen", "sayi")],
            ["İlk taslaklarda denetlenen sayı", (F6, "ozet.ilk_taslaklarda_denetlenen_sayi", "sayi")],
            ["İlk taslaklarda kaynaksız sayı", (F6, "ozet.ilk_taslaklarda_kaynaksiz_sayi", "sayi")],
            ["Süre medyanı (sn)", (F6, "ozet.sure_s_medyan", "sn")], ["Araç listesi (token)", (F6, "olcum.arac_listesi_token", "sayi")],
            ["En uzun belge sonucu, seçili çalıştırma (token)", (F6S, "olcum.en_uzun_belge_sonucu.token", "sayi")],
            ["En büyük istem, seçili çalıştırma (token)", (F6S, "ozet.istem_token_en_cok", "sayi")]]),
    ]),
    # ---- Faz 7: iki dosya = iki set (geliştirme, saklanan); hedefler listesi eşik, değer ve sonucu taşır ------------------
    ("Faz 7 · Değerlendirme düzeneği ve ilk ölçüm", [
        tablo("faz7_hedefler", "Ön yazılı hedefler", ["Hedef", "Eşik", "Geliştirme", "Saklanan", "Saklanan sonuç"],
              [[e, hd(F7S, h, "esik", "sayi"), hd(F7G, h, "deger", b), hd(F7S, h, "deger", b), hd(F7S, h, "gecti", "gecti")]
               for e, h, b in HEDEFLER[:1] + [("Kapsam dışı kayıt (zorunlu)", "kapsam_disi_kayit", "sayi")] + HEDEFLER[1:]]
              + [["Soru sayısı", None, (F7G, "ozet.genel.soru", "sayi"), (F7S, "ozet.genel.soru", "sayi"), None]],
              "Hedefler ölçümden önce yazıldı; eşik, değer ve sonuç raporların kendi 'hedefler' listesindendir."),
        tablo("faz7_saklanan", "Saklanan set ayrıntısı", ["Ölçü", "Değer"], [
            ["Soru", (F7S, "ozet.genel.soru", "sayi")], ["Araç sorusu, cevap gösterilen", (F7S, "ozet.arac.durumlar.tamam", "sayi")],
            ["Araç sorusu, doğru", (F7S, "ozet.arac.hata_turleri.dogru", "sayi")],
            ["Yetkisiz deneme, kapıya ulaşan", (F7S, ["ozet.yetkisiz.kapiya_ulasti", "ozet.yetkisiz.n"], "kesir")],
            ["Belge sorusu, belge_ara çağrılan", (F7S, "ozet.belge.belge_ara_cagrildi", "oran")],
            ["Doğrulanabilir cevap üretilemedi", (F7S, "ozet.genel.durumlar.uretemedim", "sayi")],
            ["Tekrar ölçümü: aynı durum", (F7S, ["tekrar.ayni_durum", "tekrar.soru"], "kesir")],
            ["Tekrar ölçümü: birebir aynı cevap", (F7S, ["tekrar.ayni_cevap", "tekrar.soru"], "kesir")]]),
    ]),
    # ---- Faz 8: saklanan sette üç model; eğitimin etkisi eşli karşılaştırma dosyasından ------------------------------------
    ("Faz 8 · İnce ayar (QLoRA)", [
        tablo("faz8_saklanan", "Saklanan set: kütüphane modeli, kendi temel modelimiz, ince ayarlı model",
              ["Hedef", "Eşik", "Kütüphane (Faz 7)", "Kendi temel", "İnce ayarlı"],
              [[e, hd(F7S, h, "esik", "sayi")] + [hd(d, h, "deger", b) for d in (F7S, F8T, F8A)] for e, h, b in HEDEFLER]
              + [["\"Doğrulanabilir cevap üretemedim\"", None] + [(d, "ozet.genel.durumlar.uretemedim", "sayi") for d in (F7S, F8T, F8A)],
                 ["Soru sayısı", None] + [(d, "ozet.genel.soru", "sayi") for d in (F7S, F8T, F8A)]],
              "Belge ve araç davranışları Faz 7'de saklanan set okunduktan sonra bilerek öğretildi; bu yüzden iyileşme tam genelleşme sayılmaz."),
        tablo("faz8_etki", "Eğitimin etkisi, eşli karşılaştırma (kendi temel → ince ayarlı)", ["Metrik", "İyileşen soru", "Kötüleşen soru", "p"],
              [[e, (F8K, f"eslestirme.bolum.{y}.iyilesen", "sayi"), (F8K, f"eslestirme.bolum.{y}.kotulesen", "sayi"),
                (F8K, f"eslestirme.bolum.{y}.p", "p")] for e, y in KARSILASTIRMA]),
        tablo("faz8_unutma", "Unutma ölçümü (Belebele Türkçe)", [
            "Karşılaştırma", "Soru", "Önceki doğruluk (%)", "Sonraki doğruluk (%)", "Fark", "Yalnız ilki doğru", "Yalnız ikincisi doğru", "p"],
              [[e] + [(F8U[k], f"karsilastirma.{a}", b) for a, b in [
                  ("soru", "sayi"), ("temel_dogruluk", "sayi"), ("ayarli_dogruluk", "sayi"), ("fark", "sayi"), ("yalniz_temel_dogru", "sayi"),
                  ("yalniz_ayarli_dogru", "sayi"), ("p", "p")]] for e, k in [
                  ("Kütüphane, iki ölçüm", "tekrar"), ("Kütüphane → kendi temel", "donusturme"),
                  ("Kendi temel → ince ayarlı", "ince"), ("Kütüphane → ince ayarlı", "kutuphane")]]),
        tablo("faz8_sizinti", "Eğitim sorularının Faz 7 setleriyle sızıntı denetimi", ["Ölçü", "Değer"], [
            ["Eğitim ve doğrulama sorusu", (F8Z, "soru", "sayi")], ["En yakın kalıp benzerliği", (F8Z, "en_yakin_kalip_benzerligi", "oran")],
            ["En yakın soru benzerliği", (F8Z, "en_yakin_soru_benzerligi", "oran")]]
              + [[f"İhlal: {k}", (F8Z, f"ihlal_sayisi.{k}", "sayi")] for k in ("kalip", "soru", "kimlik", "ad", "madde", "kur")]),
    ]),
    # ---- Faz 8b: nihai = canlı koşunun analizi (faz8b_analiz.json) ve kimlik koşusunun analizi (faz8b_kimlik_analiz.json).
    # *_kahin, *_uydurma_kontrolu ve faz8b_yeniden_puanlama.json koşu öncesi sınamalardır; tabloya girmez.
    ("Faz 8b · Doğrulayıcıya üç kural ve yeni test seti", [
        tablo("faz8b_onkayit", "Ön kayıtlı ölçüler (canlı koşu)", ["Ölçü", "Sonuç", "Tuttu mu"], [
            ["Kaybedilen doğru", (F8BA, "karar.kaybedilen_dogru.deger", "sayi"), (F8BA, "karar.kaybedilen_dogru.gecti", "evet_hayir")],
            ["Yanlış bayrak", (F8BA, "karar.yanlis_bayrak.deger", "sayi"), (F8BA, "karar.yanlis_bayrak.gecti", "evet_hayir")],
            ["Müşteriye özel sorularda gösterilen doğrulanmış yanlış (eski → yeni)",
             (F8BA, ["karar.musteri_yanlis_azalir.eski", "karar.musteri_yanlis_azalir.yeni"], "ok"), (F8BA, "karar.musteri_yanlis_azalir.gecti", "evet_hayir")],
            ["Süre medyanı, araç soruları (sn, eski → yeni)", (F8BA, ["karar.sure_medyan_artisi.eski_s", "karar.sure_medyan_artisi.yeni_s"], "ok"),
             (F8BA, "karar.sure_medyan_artisi.gecti", "evet_hayir")],
            ["Süre medyanı oranı (yeni / eski)", (F8BA, "karar.sure_medyan_artisi.oran", "oran"), None],
            ["Süre p90 (sn)", (F8BA, "karar.sure_p90.deger", "sn"), (F8BA, "karar.sure_p90.gecti", "evet_hayir")],
            ["Tüm şirket sızıntısı", (F8BA, "karar.tum_sirket_sizinti.deger", "sayi"), (F8BA, "karar.tum_sirket_sizinti.gecti", "evet_hayir")]]),
        tablo("faz8b_duzen", "Eski düzen ↔ yeni düzen (araç soruları)", ["Ölçü", "Eski düzen", "Yeni düzen"],
              [["Soru sayısı", (F8BA, "dogruluk.arac_eslesen.soru", "sayi"), (F8BA, "dogruluk.arac_eslesen.soru", "sayi")],
               ["Rakam doğruluğu", (F8BA, "dogruluk.arac_eslesen.eski", "oran"), (F8BA, "dogruluk.arac_eslesen.yeni", "oran")],
               ["Doğrulanmış yanlış", (F8BA, "dogruluk.arac_eslesen.eski_dogrulanmis_yanlis", "sayi"), (F8BA, "dogruluk.arac_eslesen.yeni_dogrulanmis_yanlis", "sayi")],
               ["S grubu (müşteriye özel) doğruluğu", (F8BA, "dogruluk.kirilim.grup=S.eski", "oran"), (F8BA, "dogruluk.kirilim.grup=S.yeni", "oran")],
               ["Eğitimde olan niyetler", (F8BA, "dogruluk.kirilim.egitimde=var.eski", "oran"), (F8BA, "dogruluk.kirilim.egitimde=var.yeni", "oran")],
               ["Eğitimde olmayan niyetler", (F8BA, "dogruluk.kirilim.egitimde=yok.eski", "oran"), (F8BA, "dogruluk.kirilim.egitimde=yok.yeni", "oran")],
               ["Tüm şirket, elle: ret", (F8BA, "karar.tum_sirket_el_puani.eski.ret", "sayi"), (F8BA, "karar.tum_sirket_el_puani.yeni.ret", "sayi")],
               ["Tüm şirket, elle: kapsamlı", (F8BA, "karar.tum_sirket_el_puani.eski.kapsamli", "sayi"), (F8BA, "karar.tum_sirket_el_puani.yeni.kapsamli", "sayi")],
               ["Tüm şirket, elle: yanıltıcı", (F8BA, "karar.tum_sirket_el_puani.eski.yaniltici", "sayi"), (F8BA, "karar.tum_sirket_el_puani.yeni.yaniltici", "sayi")],
               ["Kazanılan / kaybedilen soru (McNemar)", None, (F8BA, ["dogruluk.mcnemar.kazanilan#", "dogruluk.mcnemar.kaybedilen#"], "bolu")],
               ["McNemar p", None, (F8BA, "dogruluk.mcnemar.p", "p")],
               ["Kapsam düzeltmesi: düzelen / tur", None, (F8BA, ["karar.kapsam_duzeltme_basarisi.model_duzeltti", "karar.kapsam_duzeltme_basarisi.tur"], "kesir")]],
              "Eski düzen ayrı bir koşu değil, aynı koşunun kayıtlarından çıkarıldı; tek koşu."),
        tablo("faz8b_hedefler", "Faz 7 hedefleri bu sette", ["Hedef", "Eşik", "Değer", "Sonuç"],
              [[e, hd(F8BY, h, "esik", "sayi"), hd(F8BY, h, "deger", b), hd(F8BY, h, "gecti", "gecti")] for e, h, b in
               HEDEFLER[:1] + [("Kapsam dışı kayıt (zorunlu)", "kapsam_disi_kayit", "sayi")] + HEDEFLER[1:4] + HEDEFLER[7:]]),
        tablo("faz8b_kimlik_gosterilen", "Kimlik ölçümü: gösterilen cevaplardaki numaralar", ["Tür", "Yeni düzen", "Eski düzen"],
              [[e] + [(F8BK, f"gosterilen.{d}.{k}#", "sayi") for d in ("yeni", "eski")] for e, k in [
                  ("Uydurma", "uydurma"), ("Yanlış kayıt", "yanlis_kayit"), ("Doğru numara", "dogru"), ("Numarasız", "numarasiz")]]),
        tablo("faz8b_kimlik", "Kimlik ölçümü: uydurma kimlik kuralı", ["Ölçü", "Değer"], [
            ["Soru", (F8BK, "soru_sayisi", "sayi")], ["Taslak", (F8BK, "taslak_sayisi", "sayi")],
            ["Desteksiz numaralı taslak", (F8BK, "desteksiz_taslak.adet", "sayi")],
            ["Eski kuralların durdurduğu", (F8BK, "desteksiz_taslak.durduran.eski_kurallar", "sayi")],
            ["Durdurulmayan", (F8BK, "desteksiz_taslak.durduran.durdurulmadi", "sayi")],
            ["Yeni kuralın tek başına katkısı", (F8BK, "yeni_kuralin_katkisi", "metin")],
            ["Yeni kuralın yanlış bayrağı", (F8BK, "yanlis_bayrak#", "sayi")],
            ["Doğru numara sorularında doğru (yeni düzen)", (F8BK, ["D.yeni_dogru", "D.soru"], "kesir")],
            ["Doğru numara sorularında doğru (eski düzen)", (F8BK, ["D.eski_dogru", "D.soru"], "kesir")],
            ["Eğitim ↔ yeni set en yakın kalıp benzerliği", (F8BS, "denetimler.sizinti.en_yakin_kalip_benzerligi", "oran")],
            ["Eğitim ↔ yeni set en yakın soru benzerliği", (F8BS, "denetimler.sizinti.en_yakin_soru_benzerligi", "oran")]]),
    ]),
    # ---- Faz 9: nihai = faz9_karsilastirma.json (scripts/faz9_analiz.py çıktısı) ve faz9_boyut.json ----------------------
    ("Faz 9 · Araç deneyi (elle araçlar ↔ otomatik üretilen araçlar)", [
        tablo("faz9_kollar", "Üç kol, aynı sorular", ["Ölçü", "Elle araçlar", "Otomatik araçlar", "Elle + iki düzeltme (keşif)"],
              [[e] + [(F9, [f"kollar.{k}.{y}" for y in yollar] if len(yollar) > 1 else f"kollar.{k}.{yollar[0]}", b) for k in KOLLAR] for e, yollar, b in [
                  ("Tüm set, gösterilen doğru", ("tum_set.dogru", "tum_set.n"), "kesir"),
                  ("Doğrudan sınıflar (tek_cagri + zincir), gösterilen doğru", ("dogrudan.dogru", "dogrudan.n"), "kesir"),
                  ("Gösterilen yanlış (ön kayıttaki tanım)", ("tum_set.gosterilen_yanlis",), "sayi"),
                  ("baglam_siniri ile biten", ("tum_set.baglam_siniri",), "sayi"),
                  ("sure_siniri ile biten", ("tum_set.durumlar.sure_siniri",), "sayac"),
                  ("Süre medyanı / p90 (sn)", ("tum_set.sure_medyan_s", "tum_set.sure_p90_s"), "bolu"),
                  ("İstem tokenı medyanı / en büyük", ("tum_set.istem_medyan", "tum_set.istem_en_cok"), "bolu"),
                  ("Sızıntı", ("sizinti#",), "sayi")]]
              + [["Kâhin tavanı", (F9, "kahin_tavanlari.el", "metin"), (F9, "kahin_tavanlari.otomatik", "metin"), None]],
              "Üçüncü kol keşif amaçlıdır: düzeltmeler Faz 8b sonuçlarına bakılarak seçildi, p değeri yoktur."),
        tablo("faz9_siniflar", "Soru sınıflarına göre gösterilen doğru", ["Sınıf", "Soru", "Elle", "Otomatik", "Elle + düzeltme"],
              [[s, (F9, f"kollar.el.siniflar.{s}.n", "sayi")] + [(F9, f"kollar.{k}.siniflar.{s}.dogru", "sayi") for k in KOLLAR]
               for s in ("tek_cagri", "zincir", "engelli", "toplama", "yok")]),
        tablo("faz9_birincil", "Birincil ölçü: doğrudan sınıflarda eşli karşılaştırma", ["Ölçü", "Değer"], [
            ["Soru", (F9, "birincil.n", "sayi")], ["İkisi de doğru", (F9, "birincil.ikisi_dogru", "sayi")],
            ["Yalnızca elle doğru", (F9, "birincil.yalniz_el#", "sayi")], ["Yalnızca otomatik doğru", (F9, "birincil.yalniz_otomatik#", "sayi")],
            ["İkisi de yanlış", (F9, "birincil.ikisi_yanlis", "sayi")], ["Kesin McNemar p (iki yönlü)", (F9, "birincil.mcnemar_p", "p")],
            ["Ön kayıtlı karar", (F9, "birincil.karar", "metin")],
            ["Bir çift ters dönseydi p", (F9, "birincil.duyarlilik.bir_cift_donerse_p", "p")],
            ["Doğrulayıcı kör nokta soruları çıkarılırsa p", (F9, "birincil.duyarlilik.dogrulayici_kor_noktasi_haric_p", "p")],
            ["Negatif kontrol: yakalanan", (F9, "negatif_kontrol_yakalanan#", "sayi")],
            ["Üçüncü kol: yalnızca elle doğru", (F9, "ucuncu_kol.yalniz_el#", "sayi")],
            ["Üçüncü kol: yalnızca düzeltmeli doğru", (F9, "ucuncu_kol.yalniz_duzeltme#", "sayi")]]),
        tablo("faz9_boyut", "Araç listesinin bağlamdaki yeri", ["Ölçü", "Elle araçlar", "Otomatik araçlar"], [
            [e] + [(F9B, f"{k}.{y}", "sayi") for k in ("el", "otomatik")] for e, y in [
                ("Araç sayısı", "arac_sayisi"), ("Karakter", "karakter"), ("Token", "token"), ("Soru ve sonuçlara kalan token", "kalan")]]),
    ]),
    # ---- Faz 11'den sonra: kor_nokta_olcum.json (scripts/kor_nokta_olcum.py; kayıtlı taslaklar, model çalıştırılmadı) ----
    ("Faz 11'den sonra · Doğrulayıcının iki kör noktası (kayıtlı taslaklarla)", [
        tablo("kor_nokta", "Eski ve yeni doğrulayıcı, aynı kayıtlı taslaklar", ["Ölçü", "Değer"], [
            [e, (FK, f"model_toplami.{a}", "sayi")] for e, a in [
                ("Model koşularındaki taslak", "taslak"), ("Ret → kabul, beklenen değeri taşıyor", "ret_kabul_dogru"),
                ("Ret → kabul, beklenen değeri taşımıyor", "ret_kabul_eksik"),
                ("Kabul → ret, beklenen değeri taşıyor", "kabul_ret_dogru"),
                ("Kabul → ret, beklenen değeri taşımıyor", "kabul_ret_eksik"),
                ("Gösterilen cevabı doğruya dönen soru", "kazanilan_dogru_cevap"),
                ("Doğru cevabı kaybedilen soru", "kaybedilen_dogru_cevap"),
                ("Beklenen değeri taşımayan yeni gösterilen cevap", "gosterilen_yeni_eksik")]]
            + [["Kâhin koşularında kabul → ret", (FK, "kontrol_ozeti.kahin_kabul_ret", "sayi")],
               ["Uydurma kontrollerinde ret → kabul", (FK, "kontrol_ozeti.uydurma_ret_kabul", "sayi")]],
            "Sonradan yapılan ölçüm: kayıtlı taslaklar yeniden oynatıldı, model çalıştırılmadı; fazların ön kayıtlı sonuçları "
            "değişmez. Beklenen değeri taşımayan cevaplar elle okundu (README)."),
    ]),
]

# --- "Sonuçlar ve sınırlamalar": şablondaki her sayı bir manifest hücresinden gelir (elle yazılmış sayı yoktur) ----------------
SONUCLAR = [
    ("Yetkisiz erişimde sızıntı yok: Faz 3 kapı denemelerinde {a} sızıntı ({n} denemede); Faz 7 saklanan sette {b}, Faz 8 ince ayarlı modelde {c}, "
     "Faz 8b tüm şirket sorularında {d}. Faz 7 saklanan sette denemelerin {k} tanesi kapıya ulaştı; kalanlar kapıyı değil modelin kendi reddini ölçer.",
     {"a": (F3, "denemeler.sizinti", "sayi"), "n": (F3, "denemeler.toplam", "sayi"), "b": hd(F7S, "yetkisiz_sizinti", "deger", "sayi"),
      "c": hd(F8A, "yetkisiz_sizinti", "deger", "sayi"), "d": hd(F8BY, "yetkisiz_sizinti", "deger", "sayi"), "k": (F7S, "ozet.yetkisiz.kapiya_ulasti", "sayi")}),
    ("Belge arama: ayrılmış sette anlam araması ilk 5 sonuçta {a}, kabul yöntemi (kelime + anlam + sıralayıcı) {b} isabet verdi; kelime araması tek başına {c}. "
     "Belgeleri ve soruları aynı kişi yazdı, gerçek belgeler daha zor olabilir.",
     {"a": (F5, "yontemler.anlam.ayrilmis.isabet@5", "oran"), "b": (F5, "yontemler.karma_sirala.ayrilmis.isabet@5", "oran"),
      "c": (F5, "yontemler.bm25.ayrilmis.isabet@5", "oran")}),
    ("İzleme kuralları: üç tohumda geri çağırmanın en düşüğü {a}, aylık yanlış alarmın en yükseği {b} (üst sınır {c}); olayları ve kuralları aynı kişi yazdığı "
     "için bu oran gerçek dünyadaki başarıyı göstermez.",
     {"a": (F4, [f"setler.tohum{t}.olayli.puan.geri_cagirma" for t in (42, 43, 44)], "en_az_oran"),
      "b": (F4, [f"setler.tohum{t}.temiz.yanlis_alarm.aylik" for t in (42, 43, 44)], "en_cok_oran"), "c": (F4, "kabul.aylik_yanlis_alarm_en_cok", "sayi")}),
    ("Faz 7 ilk ölçümünde (saklanan set) doğrulanmış yanlış sayı {a} (sınır {b}), belge cevabı {c} ve geçerli alıntı {d} hedefi tutmadı; araç seçimi {e}, rakam doğruluğu {f} geçti.",
     {"a": hd(F7S, "dogrulanmis_yanlis_sayi", "deger", "oran"), "b": hd(F7S, "dogrulanmis_yanlis_sayi", "esik", "sayi"),
      "c": hd(F7S, "belge_cevap", "deger", "oran"), "d": hd(F7S, "belge_alinti", "deger", "oran"),
      "e": hd(F7S, "arac_secimi", "deger", "oran"), "f": hd(F7S, "rakam_dogrulugu", "deger", "oran")}),
    ("İnce ayarda belge cevabı {a} → {b}, araç seçimi {c} → {d} oldu; ama bu davranışlar Faz 7 saklanan seti okunduktan sonra bilerek öğretildi, yeni soru türüne "
     "genelleşme sayılmaz. Doğrulanmış yanlış sayı {e} ile sınırın ({f}) üstünde kaldı; rakam doğruluğundaki artışın p değeri {g}, anlamlı değil.",
     {"a": hd(F8T, "belge_cevap", "deger", "oran"), "b": hd(F8A, "belge_cevap", "deger", "oran"),
      "c": hd(F8T, "arac_secimi", "deger", "oran"), "d": hd(F8A, "arac_secimi", "deger", "oran"),
      "e": hd(F8A, "dogrulanmis_yanlis_sayi", "deger", "oran"), "f": hd(F8A, "dogrulanmis_yanlis_sayi", "esik", "sayi"),
      "g": (F8K, "eslestirme.bolum.arac.metrikler.cevap_dogrulugu.p", "p")}),
    ("Genel Türkçe korundu: Belebele doğruluğu kendi temel modelde {a}, ince ayarlıda {b}; fark {c} puan (yalnız ilki doğru {d}, yalnız ikincisi doğru {e}, p = {f}).",
     {"a": (F8U["ince"], "karsilastirma.temel_dogruluk", "sayi"), "b": (F8U["ince"], "karsilastirma.ayarli_dogruluk", "sayi"),
      "c": (F8U["ince"], "karsilastirma.fark", "sayi"), "d": (F8U["ince"], "karsilastirma.yalniz_temel_dogru", "sayi"),
      "e": (F8U["ince"], "karsilastirma.yalniz_ayarli_dogru", "sayi"), "f": (F8U["ince"], "karsilastirma.p", "p")}),
    ("Faz 8b sahiplik kuralı müşteriye özel sorularda gösterilen doğrulanmış yanlışı {a} → {b} indirdi; ama düzeltme turları süre medyanını {c} → {d} sn çıkardı "
     "(oran {e}) ve süre ölçütü tutmadı. Kazanım tek şablondan ve tek hata biçiminden geliyor, tek koşu.",
     {"a": (F8BA, "karar.musteri_yanlis_azalir.eski", "sayi"), "b": (F8BA, "karar.musteri_yanlis_azalir.yeni", "sayi"),
      "c": (F8BA, "karar.sure_medyan_artisi.eski_s", "sayi"), "d": (F8BA, "karar.sure_medyan_artisi.yeni_s", "sayi"),
      "e": (F8BA, "karar.sure_medyan_artisi.oran", "oran")}),
    ("Uydurma kimlik kuralı {n} soruluk zorlayıcı sette {k} desteksiz taslaktan hiçbirini tek başına durdurmadı (dosyadaki ifade: \"{m}\"). Asıl açık uydurma değil yanlış kayıt: "
     "gösterilen yanlış kayıt yeni düzende {y}, eski düzende {e}. Bağımsız olay sayısı çok küçük.",
     {"n": (F8BK, "soru_sayisi", "sayi"), "k": (F8BK, "desteksiz_taslak.adet", "sayi"), "m": (F8BK, "yeni_kuralin_katkisi", "metin"),
      "y": (F8BK, "gosterilen.yeni.yanlis_kayit#", "sayi"), "e": (F8BK, "gosterilen.eski.yanlis_kayit#", "sayi")}),
    ("Araç deneyi: aynı modelle elle araçlar {a} soruyu, otomatik üretilen araçlar {b} soruyu doğru cevapladı. Ön kayıtlı birincil testin p değeri {p}, doğrulayıcının "
     "kör noktası çıkarılınca {q}: yön net, anlamlılık kırılgan. Üçüncü kol yalnızca betimleyici. Bu kör nokta ve belgede yazıyla sayı "
     "sonradan kapatıldı: kayıtlı {t} taslakta {ra} doğru taslak artık geçiyor, {k} soruda gösterilen cevap doğruya dönüyor; kâhinde "
     "kabul → ret {c}, uydurma kontrollerinde ret → kabul {d}. Cevaptaki yazıyla sayılar hâlâ denetlenmiyor.",
     {"a": (F9, ["kollar.el.tum_set.dogru", "kollar.el.tum_set.n"], "kesir"), "b": (F9, ["kollar.otomatik.tum_set.dogru", "kollar.otomatik.tum_set.n"], "kesir"),
      "p": (F9, "birincil.mcnemar_p", "p"), "q": (F9, "birincil.duyarlilik.dogrulayici_kor_noktasi_haric_p", "p"),
      "t": (FK, "model_toplami.taslak", "sayi"), "ra": (FK, "model_toplami.ret_kabul_dogru", "sayi"),
      "k": (FK, "model_toplami.kazanilan_dogru_cevap", "sayi"), "c": (FK, "kontrol_ozeti.kahin_kabul_ret", "sayi"),
      "d": (FK, "kontrol_ozeti.uydurma_ret_kabul", "sayi")}),
    ("Hız sınırı: bu bilgisayarda (ekran kartsız) seçilen modelin üretim hızı saniyede {a} token; bu yüzden uzun cevaplar sürenin çoğunu alıyor ve süre sınırları "
     "buna göre konuldu. Bütün ölçümler tek makinede ve çoğunlukla tek koşuyla yapıldı.",
     {"a": (F0, "adaylar[model=qwen3.5:9b].uretim_tps", "iki")}),
]


class ManifestHatasi(Exception):
    """Manifestteki bir dosya ya da anahtar eksik."""


# --- Okuma ve biçimlendirme ----------------------------------------------------------------------------------------------
def _grupla(metin: str) -> str:
    return metin.replace(",", "X").replace(".", ",").replace("X", ".")


def _sade(x) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if isinstance(x, float) and not x.is_integer():
        return _grupla(f"{x:,.4f}".rstrip("0").rstrip("."))
    return _grupla(f"{int(x):,}") if abs(x) >= 1000 else str(int(x))


def _ondalik(x, n: int) -> str:
    return _grupla(f"{float(x):,.{n}f}")


def bicimle(bicim: str, v) -> str:
    """Ham değeri (bazı biçimlerde değer listesini) rapor metnine çevirir."""
    if bicim == "kesir":
        return f"{_sade(v[0])}/{_sade(v[1])}"
    if bicim == "ok":
        return f"{_sade(v[0])} → {_sade(v[1])}"
    if bicim == "bolu":
        return f"{_sade(v[0])} / {_sade(v[1])}"
    if bicim in ("en_az_oran", "en_cok_oran"):
        return _ondalik((min if bicim == "en_az_oran" else max)(v), 3)
    if v is None:
        return "–"
    if bicim == "evet_hayir":
        return "evet" if v else "hayır"
    if bicim == "gecti":
        return "Geçti" if v else "Kaldı"
    return {"sayi": _sade, "metin": str, "sayac": _sade, "oran": lambda x: _ondalik(x, 3), "p": lambda x: _ondalik(x, 4),
            "sn": lambda x: _ondalik(x, 1), "iki": lambda x: _ondalik(x, 2),
            "kurus_tl": lambda x: _ondalik(int(x) / 100, 2)}[bicim](v)


def _oku(veri, yol: str):
    """Noktalı yolu veride izler; bulunamayan parçayı KeyError ile bildirir."""
    uzunluk = yol.endswith("#")
    simdiki, izlenen = veri, ""
    for parca in re.findall(r"\[[^\]]*\]|[^.\[\]#]+", yol):
        izlenen += parca if parca.startswith("[") else ("." + parca if izlenen else parca)
        try:
            if parca.startswith("["):
                ic = parca[1:-1]
                if "=" in ic:
                    alan, deger = ic.split("=", 1)
                    simdiki = next(x for x in simdiki if x[alan] == deger)
                elif ic.isdigit():
                    simdiki = simdiki[int(ic)]
                else:
                    simdiki = simdiki[ic]
            else:
                simdiki = simdiki[parca]
        except (KeyError, IndexError, StopIteration, TypeError):
            raise KeyError(izlenen) from None
    return len(simdiki) if uzunluk else simdiki


class Okuyucu:
    def __init__(self, klasor: Path):
        self.klasor, self.onbellek = klasor, {}

    def dosya(self, ad: str):
        if ad not in self.onbellek:
            yol = self.klasor / ad
            if not yol.is_file():
                raise ManifestHatasi(f"manifest dosyası yok: {ad} (aranan yer: {yol})")
            try:
                self.onbellek[ad] = json.loads(yol.read_text(encoding="utf-8"))
            except ValueError as e:
                raise ManifestHatasi(f"manifest dosyası okunamadı: {ad} ({e})") from None
        return self.onbellek[ad]

    def deger(self, hucre):
        """(dosya, yol veya yol listesi, biçim) hücresinin ham değeri."""
        dosya, yol, bicim = hucre
        veri = self.dosya(dosya)
        try:
            if isinstance(yol, (list, tuple)):
                return [_oku(veri, y) for y in yol]
            if bicim == "sayac":  # üst sözlük zorunlu, yalnızca son anahtar yoksa 0
                ust, _, son = yol.rpartition(".")
                return _oku(veri, ust).get(son, 0)
            return _oku(veri, yol)
        except KeyError as e:
            raise ManifestHatasi(f"manifest anahtarı yok: {dosya} → {e.args[0]}") from None


def hucre_isle(okuyucu: Okuyucu, hucre):
    """Hücreyi (ham değer, gösterim) çiftine çevirir; None boş hücredir."""
    if hucre is None:
        return None, ""
    if isinstance(hucre, str):
        return hucre, hucre
    ham = okuyucu.deger(hucre)
    return ham, bicimle(hucre[2], ham)


def dosyalar(hucre) -> list[str]:
    return [hucre[0]] if isinstance(hucre, tuple) and len(hucre) == 3 and isinstance(hucre[0], str) else []


def manifest_dosyalari() -> list[str]:
    """Manifestin okuduğu bütün dosyalar (sıralı, tekrarsız)."""
    hucreler = [h for _, ts in MANIFEST for t in ts for satir in t["satirlar"] for h in satir]
    hucreler += [h for _, hs in SONUCLAR for h in hs.values()]
    return sorted({d for h in hucreler for d in dosyalar(h)})


def uret(okuyucu: Okuyucu) -> tuple[list[dict], list[dict], list[str]]:
    """Bütün tabloları ve sonuç maddelerini üretir; eksikleri toplayıp sonda tek hatayla bildirir."""
    hatalar, bolumler, sonuclar = [], [], []

    def guvenli(hucre):
        try:
            return hucre_isle(okuyucu, hucre)
        except ManifestHatasi as e:
            if str(e) not in hatalar:
                hatalar.append(str(e))
            return None, "?"

    for baslik, tablolar in MANIFEST:
        cikti = []
        for t in tablolar:
            satirlar, kaynaklar = [], []
            for satir in t["satirlar"]:
                islenen = [guvenli(h) for h in satir]
                for h in satir:
                    kaynaklar += [d for d in dosyalar(h) if d not in kaynaklar]
                satirlar.append({"ad": satir[0], "degerler": [i[0] for i in islenen[1:]], "gosterim": [i[1] for i in islenen[1:]]})
            cikti.append({"id": t["id"], "baslik": t["baslik"], "not": t["not"], "kolonlar": t["kolonlar"], "kaynaklar": kaynaklar, "satirlar": satirlar})
        bolumler.append({"baslik": baslik, "tablolar": cikti})
    for sablon, hucreler in SONUCLAR:
        gosterim = {ad: guvenli(h)[1] for ad, h in hucreler.items()}
        sonuclar.append({"metin": sablon.format(**gosterim), "kaynaklar": sorted({d for h in hucreler.values() for d in dosyalar(h)})})
    if hatalar:
        raise ManifestHatasi("\n".join(hatalar))
    return bolumler, sonuclar, hatalar


# --- Çıktı ---------------------------------------------------------------------------------------------------------------
def _satir_md(hucreler) -> str:
    return "| " + " | ".join(str(h).replace("|", "\\|") for h in hucreler) + " |"


def markdown(bolumler: list[dict], sonuclar: list[dict]) -> str:
    s = ["# Sayman: ölçüm raporu", "",
         "Bu rapor `scripts/faz11_rapor.py` ile `reports/` altındaki ölçüm dosyalarından üretilir; hiçbir ölçüm yeniden çalıştırılmaz. "
         "Her tablonun altında okunan dosyalar yazılıdır; hangi tablonun hangi dosya ve anahtardan okunduğu betikteki MANIFEST'tedir. "
         "Tablolardaki ve sonuç maddelerindeki sayılar dosyalardan okunur.", ""]
    for b in bolumler:
        s += [f"## {b['baslik']}", ""]
        for t in b["tablolar"]:
            s += [f"### {t['baslik']}", ""]
            if t["not"]:
                s += [t["not"], ""]
            s += [_satir_md(t["kolonlar"]), _satir_md(["---"] * len(t["kolonlar"]))]
            s += [_satir_md([r["ad"]] + r["gosterim"]) for r in t["satirlar"]]
            s += ["", "Kaynak: " + ", ".join(f"`{k}`" for k in t["kaynaklar"]), ""]
    s += ["## Sonuçlar ve sınırlamalar", ""] + [f"- {x['metin']}" for x in sonuclar]
    return "\n".join(s) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Faz 11: ölçüm raporunu yeniden üretir.")
    ap.add_argument("--rapor-klasoru", type=Path, default=KOK / "reports", help="ölçüm dosyalarının klasörü")
    ap.add_argument("--cikti", type=Path, default=KOK / "reports" / "faz11_rapor.md", help="Markdown çıktısı (JSON yanına .json olarak yazılır)")
    a = ap.parse_args(argv)
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        bolumler, sonuclar, _ = uret(Okuyucu(a.rapor_klasoru))
    except ManifestHatasi as e:
        print(f"HATA: rapor üretilemedi, çıktı yazılmadı.\n{e}", file=sys.stderr)
        return 1
    a.cikti.parent.mkdir(parents=True, exist_ok=True)
    with open(a.cikti, "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown(bolumler, sonuclar))
    with open(a.cikti.with_suffix(".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"bolumler": bolumler, "sonuclar": sonuclar}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{a.cikti} ve {a.cikti.with_suffix('.json')} yazıldı ({sum(len(b['tablolar']) for b in bolumler)} tablo, {len(sonuclar)} sonuç maddesi).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
