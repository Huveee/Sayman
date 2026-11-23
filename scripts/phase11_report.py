"""Phase 11 report: regenerates all the metric tables from the measurement files under reports/ with a single command.

Which table is read from which file and which key is written explicitly in the MANIFEST below. The "latest run" is decided
by this list, not by the file modification time. If a phase has several report versions, the file the README presents as the
final result has been chosen (the reason is in the comment above the relevant section). No measurement is run again; the
script only reads JSON files.

If a manifest file or key is missing, which one is missing is printed and the exit code is not 0; there is no default value.
The only exception is the "sayac" format: in the status counters (e.g. durumlar.baglam_siniri) a status key that was never seen
does not exist; this format requires the parent dictionary to exist and counts 0 only if just the last key is missing. The
output has no timestamp: the same input gives exactly the same output. The dates are only the measurement files' own dates.

Path syntax: "a.b.c" follows dictionary keys; "list[0]" reads by index, "list[ad=x]" by field match, "dict[a.b]" by a dotted
key, and a trailing "#" by length.

Output: reports/faz11_rapor_guncel.md and reports/faz11_rapor_guncel.json. Phase 11's own output
(reports/faz11_rapor.md and .json, commit 51c634c) is kept and not overwritten; the current file also contains the
measurements added after it (the verifier blind spot measurement).

    .venv\\Scripts\\python -I scripts\\phase11_report.py
    .venv\\Scripts\\python -I scripts\\phase11_report.py --rapor-klasoru BASKA_KLASOR --cikti BASKA\\rapor.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- File names ----------------------------------------------------------------------------------------------------
PHASE0_SPEED, PHASE2_METRICS, PHASE3_TOOLS, PHASE4_MONITORING, PHASE5_SEARCH, PHASE5_RESOURCES = "faz0_hiz.json", "faz2_metrikler.json", "faz3_araclar.json", "faz4_izleme.json", "faz5_arama.json", "faz5_kaynak.json"
PHASE6_SMOKE_FIRST, PHASE6_SMOKE, PHASE6_SMOKE_SELECTED = "faz6_duman_ilk.json", "faz6_duman.json", "faz6_duman_secili.json"
PHASE7_DEVELOPMENT, PHASE7_HELD_OUT = "faz7_olcum_gelistirme.json", "faz7_olcum_saklanan.json"
PHASE8_BASE, PHASE8_TUNED, PHASE8_COMPARISON = "faz8_temel_kendi_olcum_saklanan.json", "faz8_ayarli_olcum_saklanan.json", "faz8_karsilastirma_egitim_saklanan.json"
PHASE8_LEAKAGE = "faz8_sizinti.json"
PHASE8_FORGETTING = {"tekrar": "faz8_unutma_tekrar.json", "donusturme": "faz8_unutma_donusturme.json",
       "ince": "faz8_unutma.json", "kutuphane": "faz8_unutma_kutuphane_ayarli.json"}
PHASE8B_ANALYSIS, PHASE8B_TUNED_NEW, PHASE8B_IDENTIFIER_ANALYSIS, PHASE8B_QUESTION_SET = "faz8b_analiz.json", "faz8b_ayarli_olcum_yeni.json", "faz8b_kimlik_analiz.json", "faz8b_soru_seti.json"
PHASE9_COMPARISON, PHASE9_SIZE = "faz9_karsilastirma.json", "faz9_boyut.json"
BLIND_SPOT_MEASURE = "kor_nokta_olcum.json"

# --- Manifest helpers: cell = (file, path or list of paths, format); None = empty cell -----------------------------
ARMS = ("el", "otomatik", "duzeltme")


def target_cell(file, name, field, fmt):
    """A field of one target from the 'hedefler' list of a measurement report."""
    return (file, f"hedefler[ad={name}].{field}", fmt)


def table(id_, title, columns, rows, not_=""):
    return {"id": id_, "baslik": title, "kolonlar": columns, "satirlar": rows, "not": not_}


# Phase 7/8 target rows: (label, target name, value format)
TARGETS = [("Yetkisiz sızıntı (zorunlu)", "yetkisiz_sizinti", "sayi"), ("Araç seçimi", "arac_secimi", "oran"),
            ("Rakam doğruluğu", "rakam_dogrulugu", "oran"), ("Doğrulanmış yanlış sayı", "dogrulanmis_yanlis_sayi", "oran"),
            ("Belge isabeti", "belge_isabet", "oran"), ("Belge cevabı", "belge_cevap", "oran"),
            ("Belge alıntısı", "belge_alinti", "oran"), ("Süre medyanı (sn)", "sure_medyan", "sn"),
            ("Süre p90 (sn)", "sure_p90", "sn")]
COMPARISON_METRICS = [("Araç seçimi (araç soruları)", "arac.metrikler.arac_secimi"), ("Parametreler (araç)", "arac.metrikler.parametre"),
                 ("Rakam doğruluğu (araç)", "arac.metrikler.cevap_dogrulugu"),
                 ("Doğrulanmış yanlış sayı (araç)", "arac.metrikler.dogrulanmis_yanlis_sayi"),
                 ("Doğru madde bulundu (belge)", "belge.metrikler.isabet"), ("Belge cevabı", "belge.metrikler.cevap_dogrulugu"),
                 ("Geçerli alıntı (belge)", "belge.metrikler.alinti_gecerli"), ("Yetkisiz denemede ret", "yetkisiz.metrikler.reddetti")]

MANIFEST = [
    # ---- Phase 0: one file, one version --------------------------------------------------------------------------
    ("Faz 0 · Model hız ölçümü", [
        table("faz0_hiz", "İki aday modelin hızı (bu bilgisayar, ekran kartsız)", ["Ölçüt", "Aday 1", "Aday 2"], [
            [e] + [(PHASE0_SPEED, f"adaylar[{i}].{k}", b) for i in (0, 1)] for e, k, b in [
                ("Model", "model", "metin"), ("Boyut (GB)", "boyut_gb", "iki"), ("Bellekteki boyut (GB)", "bellekteki_boyut_gb", "iki"),
                ("Soğuk tur (sn)", "soguk_tur_s", "sn"), ("İstem işleme (token/sn)", "istem_isleme_tps", "iki"),
                ("Üretim (token/sn)", "uretim_tps", "iki"), ("Ortanca tur (sn)", "ortanca_tur_s", "sn"),
                ("Geçerli araç çağrısı", "gecerli_sayisi", "sayi"), ("Kural sağlandı", "kural_saglandi", "evet_hayir")]],
              "Kural: ön bellek ısındıktan sonra her araç çağırma turu eşik süreyi aşmayacak, geçerli çağrı sayısı yeterli olacak."),
        table("faz0_secim", "Seçim", ["Ölçüt", "Değer"], [
            ["Seçilen model", (PHASE0_SPEED, "secilen_model", "metin")], ["Tur süresi eşiği (sn)", (PHASE0_SPEED, "kural.sure_esigi_s", "sayi")],
            ["Gereken geçerli çağrı", (PHASE0_SPEED, "kural.gereken_gecerli", "sayi")]]),
    ]),
    # ---- Phase 2: metric summary of the warehouse "now" (simulation day); the independent pandas check is in the tests ---
    ("Faz 2 · Veri ambarı ve metrikler", [
        table("faz2_metrikler", "Ambarın hesapladığı metrikler", ["Metrik", "Değer"], [
            ["Gün", (PHASE2_METRICS, "tarih", "metin")], ["Nakit pozisyonu (TL)", (PHASE2_METRICS, "nakit_pozisyonu.toplam_try_kurus", "kurus_tl")],
            ["Alacak yaşlandırma toplamı (TL)", (PHASE2_METRICS, "yaslandirma_toplam.toplam_kurus", "kurus_tl")],
            ["Yaşlandırmadaki müşteri sayısı", (PHASE2_METRICS, "yaslandirma_musteri_sayisi", "sayi")],
            ["DSO (gün)", (PHASE2_METRICS, "dso.dso_gun", "iki")], ["DSO, 2025 yıl sonu (gün)", (PHASE2_METRICS, "dso_2025_yil_sonu.dso_gun", "iki")],
            ["Geciken fatura sayısı", (PHASE2_METRICS, "geciken_alacaklar.fatura_sayisi", "sayi")],
            ["Geciken alacak (TL)", (PHASE2_METRICS, "geciken_alacaklar.toplam_try_kurus", "kurus_tl")],
            ["Yaklaşan ödemelerin faturası", (PHASE2_METRICS, "yaklasan_odemeler.fatura_sayisi", "sayi")],
            ["Yaklaşan ödemeler (TL)", (PHASE2_METRICS, "yaklasan_odemeler.toplam_kurus", "kurus_tl")],
            ["Kur riski (TL)", (PHASE2_METRICS, "kur_riski.toplam_try_kurus", "kurus_tl")],
            ["Nakit akış tahmini, hafta sayısı", (PHASE2_METRICS, "nakit_akis_tahmini.hafta_sayisi", "sayi")],
            ["Nakit akış tahmini, açılış (TL)", (PHASE2_METRICS, "nakit_akis_tahmini.acilis_kurus", "kurus_tl")]],
              "Bu değerler ambarın çıktısıdır; her metriğin bağımsız pandas hesaplamasıyla karşılaştırması testlerdedir."),
    ]),
    # ---- Phase 3 -------------------------------------------------------------------------------------------------
    ("Faz 3 · Araçlar ve yetki", [
        table("faz3_yetki", "Yetkisiz denemeler ve denetim kaydı", ["Ölçüt", "Değer"], [
            ["Araç sayısı", (PHASE3_TOOLS, "araclar#", "sayi")], ["Yetkisiz deneme", (PHASE3_TOOLS, "denemeler.toplam", "sayi")],
            ["Beklenen sonucu veren deneme", (PHASE3_TOOLS, "denemeler.sorunsuz", "sayi")], ["Sızıntı", (PHASE3_TOOLS, "denemeler.sizinti", "sayi")],
            ["Denetim kaydı satırı", (PHASE3_TOOLS, "denetim_kaydi.satir", "sayi")], ["Zincir hatası", (PHASE3_TOOLS, "denetim_kaydi.zincir_hatalari#", "sayi")],
            ["Araç listesi (token)", (PHASE3_TOOLS, "token_olcumu.arac_listesi", "sayi")], ["En büyük cevap (token)", (PHASE3_TOOLS, "token_olcumu.en_buyuk_cevap", "sayi")],
            ["Kapıda en yavaş çağrı (ms)", (PHASE3_TOOLS, "sureler.kapida_en_yavas_ms", "sn")]]),
    ]),
    # ---- Phase 4: tuning seed 42, held-out seeds 43 and 44 -------------------------------------------------------------
    ("Faz 4 · İzleme ve uyarılar", [
        table("faz4_izleme", "Gömülü olayları yakalama ve olaysız veride yanlış alarm", [
            "Tohum", "Ayrılmış mı", "Olay", "Yakalanan", "Geri çağırma", "Aylık yanlış alarm"],
              [[f"Tohum {t}", (PHASE4_MONITORING, f"setler.tohum{t}.ayrilmis", "evet_hayir"), (PHASE4_MONITORING, f"setler.tohum{t}.olayli.puan.olay", "sayi"),
                (PHASE4_MONITORING, f"setler.tohum{t}.olayli.puan.yakalanan", "sayi"), (PHASE4_MONITORING, f"setler.tohum{t}.olayli.puan.geri_cagirma", "oran"),
                (PHASE4_MONITORING, f"setler.tohum{t}.temiz.yanlis_alarm.aylik", "oran")] for t in (42, 43, 44)]
              + [["Kabul eşiği", None, None, None, (PHASE4_MONITORING, "kabul.geri_cagirma_en_az", "sayi"), (PHASE4_MONITORING, "kabul.aylik_yanlis_alarm_en_cok", "sayi")]],
              "Olayları ve kuralları aynı kişi yazdı; yüksek yakalama oranı gerçek dünyadaki başarıyı göstermez."),
    ]),
    # ---- Phase 5: the 4-method table in the README = faz5_arama.json; memory and duration = faz5_kaynak.json ----------
    ("Faz 5 · Belge arama", [
        table("faz5_arama", "Arama yöntemleri (ilk 5 sonuçta doğru madde)", [
            "Yöntem", "Ayar seti, ilk 5", "Ayrılmış set, ilk 5", "Ayrılmış, ilk sırada", "Ayrılmış soru", "Ayrılmış, kaçan"],
              [[e, (PHASE5_SEARCH, f"yontemler.{y}.ayar.isabet@5", "oran"), (PHASE5_SEARCH, f"yontemler.{y}.ayrilmis.isabet@5", "oran"),
                (PHASE5_SEARCH, f"yontemler.{y}.ayrilmis.isabet@1", "oran"), (PHASE5_SEARCH, f"yontemler.{y}.ayrilmis.soru", "sayi"),
                (PHASE5_SEARCH, f"yontemler.{y}.ayrilmis.kacan", "sayi")] for e, y in [
                  ("Kelime araması (BM25)", "bm25"), ("Anlam araması (bge-m3)", "anlam"), ("Kelime + anlam", "karma"),
                  ("Kelime + anlam + sıralayıcı (kabul yöntemi)", "karma_sirala")]]
              + [["Kabul ölçütü sağlandı", None, (PHASE5_SEARCH, "kabul", "evet_hayir"), None, None, None]]),
        table("faz5_kaynak", "Bellek ve süre (sohbet modeli, anlam modeli ve sıralayıcı birlikte)", ["Ölçüt", "Değer"], [
            ["Toplam bellek (GB)", (PHASE5_RESOURCES, "toplam_bellek_gb", "sn")], ["Üçü yüklüyken boş bellek (GB)", (PHASE5_RESOURCES, "bos_bellek_gb.hepsi_yuklu", "iki")],
            ["Sıralayıcı süreci, arama sonrası (GB)", (PHASE5_RESOURCES, "surec_bellek_gb.arama_sonrasi", "iki")],
            ["Sıralayıcılı arama, soru başına medyan (sn)", (PHASE5_RESOURCES, "arama_soru_basina_s.medyan", "sn")],
            ["Sıralayıcılı arama, en çok (sn)", (PHASE5_RESOURCES, "arama_soru_basina_s.en_cok", "sn")],
            ["Sohbet modeli bellekten atıldı mı", (PHASE5_RESOURCES, "sohbet_modeli_atildi_mi", "evet_hayir")]]),
    ]),
    # ---- Phase 6: the second run (faz6_duman.json) is the README's main result; the first and the selected run beside it ---
    ("Faz 6 · Doğrulayıcı ve orkestratör (duman testi)", [
        table("faz6_durumlar", "Duman testinin bitiş durumları", ["Çalıştırma", "tamam", "ret", "uretemedim", "sure_siniri", "baglam_siniri"], [
            [e] + [(d, f"ozet.durumlar.{k}", "sayac") for k in ("tamam", "ret", "uretemedim", "sure_siniri", "baglam_siniri")]
            for e, d in [("İlk çalıştırma", PHASE6_SMOKE_FIRST), ("İkinci çalıştırma (doğrulayıcı düzeltmelerinden sonra)", PHASE6_SMOKE), ("Seçili sorular, yeniden", PHASE6_SMOKE_SELECTED)]],
              "Bu bir kalite ölçümü değildir; sorular azdır ve düzeltmeler aynı sorularla yapıldı."),
        table("faz6_olcum", "İkinci çalıştırmanın ölçümleri", ["Ölçüt", "Değer"], [
            ["Soru", (PHASE6_SMOKE, "ozet.soru", "sayi")], ["İlk taslağı reddedilen soru", (PHASE6_SMOKE, "ozet.ilk_taslagi_reddedilen", "sayi")],
            ["İlk taslaklarda denetlenen sayı", (PHASE6_SMOKE, "ozet.ilk_taslaklarda_denetlenen_sayi", "sayi")],
            ["İlk taslaklarda kaynaksız sayı", (PHASE6_SMOKE, "ozet.ilk_taslaklarda_kaynaksiz_sayi", "sayi")],
            ["Süre medyanı (sn)", (PHASE6_SMOKE, "ozet.sure_s_medyan", "sn")], ["Araç listesi (token)", (PHASE6_SMOKE, "olcum.arac_listesi_token", "sayi")],
            ["En uzun belge sonucu, seçili çalıştırma (token)", (PHASE6_SMOKE_SELECTED, "olcum.en_uzun_belge_sonucu.token", "sayi")],
            ["En büyük istem, seçili çalıştırma (token)", (PHASE6_SMOKE_SELECTED, "ozet.istem_token_en_cok", "sayi")]]),
    ]),
    # ---- Phase 7: two files = two sets (gelistirme, saklanan); the targets list carries threshold, value and result -------
    ("Faz 7 · Değerlendirme düzeneği ve ilk ölçüm", [
        table("faz7_hedefler", "Ön yazılı hedefler", ["Hedef", "Eşik", "Geliştirme", "Saklanan", "Saklanan sonuç"],
              [[e, target_cell(PHASE7_HELD_OUT, h, "esik", "sayi"), target_cell(PHASE7_DEVELOPMENT, h, "deger", b), target_cell(PHASE7_HELD_OUT, h, "deger", b), target_cell(PHASE7_HELD_OUT, h, "gecti", "gecti")]
               for e, h, b in TARGETS[:1] + [("Kapsam dışı kayıt (zorunlu)", "kapsam_disi_kayit", "sayi")] + TARGETS[1:]]
              + [["Soru sayısı", None, (PHASE7_DEVELOPMENT, "ozet.genel.soru", "sayi"), (PHASE7_HELD_OUT, "ozet.genel.soru", "sayi"), None]],
              "Hedefler ölçümden önce yazıldı; eşik, değer ve sonuç raporların kendi 'hedefler' listesindendir."),
        table("faz7_saklanan", "Saklanan set ayrıntısı", ["Ölçü", "Değer"], [
            ["Soru", (PHASE7_HELD_OUT, "ozet.genel.soru", "sayi")], ["Araç sorusu, cevap gösterilen", (PHASE7_HELD_OUT, "ozet.arac.durumlar.tamam", "sayi")],
            ["Araç sorusu, doğru", (PHASE7_HELD_OUT, "ozet.arac.hata_turleri.dogru", "sayi")],
            ["Yetkisiz deneme, kapıya ulaşan", (PHASE7_HELD_OUT, ["ozet.yetkisiz.kapiya_ulasti", "ozet.yetkisiz.n"], "kesir")],
            ["Belge sorusu, belge_ara çağrılan", (PHASE7_HELD_OUT, "ozet.belge.belge_ara_cagrildi", "oran")],
            ["Doğrulanabilir cevap üretilemedi", (PHASE7_HELD_OUT, "ozet.genel.durumlar.uretemedim", "sayi")],
            ["Tekrar ölçümü: aynı durum", (PHASE7_HELD_OUT, ["tekrar.ayni_durum", "tekrar.soru"], "kesir")],
            ["Tekrar ölçümü: birebir aynı cevap", (PHASE7_HELD_OUT, ["tekrar.ayni_cevap", "tekrar.soru"], "kesir")]]),
    ]),
    # ---- Phase 8: three models on the held-out set; the effect of training from the paired comparison file -----------------
    ("Faz 8 · İnce ayar (QLoRA)", [
        table("faz8_saklanan", "Saklanan set: kütüphane modeli, kendi temel modelimiz, ince ayarlı model",
              ["Hedef", "Eşik", "Kütüphane (Faz 7)", "Kendi temel", "İnce ayarlı"],
              [[e, target_cell(PHASE7_HELD_OUT, h, "esik", "sayi")] + [target_cell(d, h, "deger", b) for d in (PHASE7_HELD_OUT, PHASE8_BASE, PHASE8_TUNED)] for e, h, b in TARGETS]
              + [["\"Doğrulanabilir cevap üretemedim\"", None] + [(d, "ozet.genel.durumlar.uretemedim", "sayi") for d in (PHASE7_HELD_OUT, PHASE8_BASE, PHASE8_TUNED)],
                 ["Soru sayısı", None] + [(d, "ozet.genel.soru", "sayi") for d in (PHASE7_HELD_OUT, PHASE8_BASE, PHASE8_TUNED)]],
              "Belge ve araç davranışları Faz 7'de saklanan set okunduktan sonra bilerek öğretildi; bu yüzden iyileşme tam genelleşme sayılmaz."),
        table("faz8_etki", "Eğitimin etkisi, eşli karşılaştırma (kendi temel → ince ayarlı)", ["Metrik", "İyileşen soru", "Kötüleşen soru", "p"],
              [[e, (PHASE8_COMPARISON, f"eslestirme.bolum.{y}.iyilesen", "sayi"), (PHASE8_COMPARISON, f"eslestirme.bolum.{y}.kotulesen", "sayi"),
                (PHASE8_COMPARISON, f"eslestirme.bolum.{y}.p", "p")] for e, y in COMPARISON_METRICS]),
        table("faz8_unutma", "Unutma ölçümü (Belebele Türkçe)", [
            "Karşılaştırma", "Soru", "Önceki doğruluk (%)", "Sonraki doğruluk (%)", "Fark", "Yalnız ilki doğru", "Yalnız ikincisi doğru", "p"],
              [[e] + [(PHASE8_FORGETTING[k], f"karsilastirma.{a}", b) for a, b in [
                  ("soru", "sayi"), ("temel_dogruluk", "sayi"), ("ayarli_dogruluk", "sayi"), ("fark", "sayi"), ("yalniz_temel_dogru", "sayi"),
                  ("yalniz_ayarli_dogru", "sayi"), ("p", "p")]] for e, k in [
                  ("Kütüphane, iki ölçüm", "tekrar"), ("Kütüphane → kendi temel", "donusturme"),
                  ("Kendi temel → ince ayarlı", "ince"), ("Kütüphane → ince ayarlı", "kutuphane")]]),
        table("faz8_sizinti", "Eğitim sorularının Faz 7 setleriyle sızıntı denetimi", ["Ölçü", "Değer"], [
            ["Eğitim ve doğrulama sorusu", (PHASE8_LEAKAGE, "soru", "sayi")], ["En yakın kalıp benzerliği", (PHASE8_LEAKAGE, "en_yakin_kalip_benzerligi", "oran")],
            ["En yakın soru benzerliği", (PHASE8_LEAKAGE, "en_yakin_soru_benzerligi", "oran")]]
              + [[f"İhlal: {k}", (PHASE8_LEAKAGE, f"ihlal_sayisi.{k}", "sayi")] for k in ("kalip", "soru", "kimlik", "ad", "madde", "kur")]),
    ]),
    # ---- Phase 8b: final = the analysis of the live run (faz8b_analiz.json) and of the identifier run (faz8b_kimlik_analiz.json).
    # *_kahin, *_uydurma_kontrolu and faz8b_yeniden_puanlama.json are checks made before the run; they do not enter a table.
    ("Faz 8b · Doğrulayıcıya üç kural ve yeni test seti", [
        table("faz8b_onkayit", "Ön kayıtlı ölçüler (canlı koşu)", ["Ölçü", "Sonuç", "Tuttu mu"], [
            ["Kaybedilen doğru", (PHASE8B_ANALYSIS, "karar.kaybedilen_dogru.deger", "sayi"), (PHASE8B_ANALYSIS, "karar.kaybedilen_dogru.gecti", "evet_hayir")],
            ["Yanlış bayrak", (PHASE8B_ANALYSIS, "karar.yanlis_bayrak.deger", "sayi"), (PHASE8B_ANALYSIS, "karar.yanlis_bayrak.gecti", "evet_hayir")],
            ["Müşteriye özel sorularda gösterilen doğrulanmış yanlış (eski → yeni)",
             (PHASE8B_ANALYSIS, ["karar.musteri_yanlis_azalir.eski", "karar.musteri_yanlis_azalir.yeni"], "ok"), (PHASE8B_ANALYSIS, "karar.musteri_yanlis_azalir.gecti", "evet_hayir")],
            ["Süre medyanı, araç soruları (sn, eski → yeni)", (PHASE8B_ANALYSIS, ["karar.sure_medyan_artisi.eski_s", "karar.sure_medyan_artisi.yeni_s"], "ok"),
             (PHASE8B_ANALYSIS, "karar.sure_medyan_artisi.gecti", "evet_hayir")],
            ["Süre medyanı oranı (yeni / eski)", (PHASE8B_ANALYSIS, "karar.sure_medyan_artisi.oran", "oran"), None],
            ["Süre p90 (sn)", (PHASE8B_ANALYSIS, "karar.sure_p90.deger", "sn"), (PHASE8B_ANALYSIS, "karar.sure_p90.gecti", "evet_hayir")],
            ["Tüm şirket sızıntısı", (PHASE8B_ANALYSIS, "karar.tum_sirket_sizinti.deger", "sayi"), (PHASE8B_ANALYSIS, "karar.tum_sirket_sizinti.gecti", "evet_hayir")]]),
        table("faz8b_duzen", "Eski düzen ↔ yeni düzen (araç soruları)", ["Ölçü", "Eski düzen", "Yeni düzen"],
              [["Soru sayısı", (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.soru", "sayi"), (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.soru", "sayi")],
               ["Rakam doğruluğu", (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.eski", "oran"), (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.yeni", "oran")],
               ["Doğrulanmış yanlış", (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.eski_dogrulanmis_yanlis", "sayi"), (PHASE8B_ANALYSIS, "dogruluk.arac_eslesen.yeni_dogrulanmis_yanlis", "sayi")],
               ["S grubu (müşteriye özel) doğruluğu", (PHASE8B_ANALYSIS, "dogruluk.kirilim.grup=S.eski", "oran"), (PHASE8B_ANALYSIS, "dogruluk.kirilim.grup=S.yeni", "oran")],
               ["Eğitimde olan niyetler", (PHASE8B_ANALYSIS, "dogruluk.kirilim.egitimde=var.eski", "oran"), (PHASE8B_ANALYSIS, "dogruluk.kirilim.egitimde=var.yeni", "oran")],
               ["Eğitimde olmayan niyetler", (PHASE8B_ANALYSIS, "dogruluk.kirilim.egitimde=yok.eski", "oran"), (PHASE8B_ANALYSIS, "dogruluk.kirilim.egitimde=yok.yeni", "oran")],
               ["Tüm şirket, elle: ret", (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.eski.ret", "sayi"), (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.yeni.ret", "sayi")],
               ["Tüm şirket, elle: kapsamlı", (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.eski.kapsamli", "sayi"), (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.yeni.kapsamli", "sayi")],
               ["Tüm şirket, elle: yanıltıcı", (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.eski.yaniltici", "sayi"), (PHASE8B_ANALYSIS, "karar.tum_sirket_el_puani.yeni.yaniltici", "sayi")],
               ["Kazanılan / kaybedilen soru (McNemar)", None, (PHASE8B_ANALYSIS, ["dogruluk.mcnemar.kazanilan#", "dogruluk.mcnemar.kaybedilen#"], "bolu")],
               ["McNemar p", None, (PHASE8B_ANALYSIS, "dogruluk.mcnemar.p", "p")],
               ["Kapsam düzeltmesi: düzelen / tur", None, (PHASE8B_ANALYSIS, ["karar.kapsam_duzeltme_basarisi.model_duzeltti", "karar.kapsam_duzeltme_basarisi.tur"], "kesir")]],
              "Eski düzen ayrı bir koşu değil, aynı koşunun kayıtlarından çıkarıldı; tek koşu."),
        table("faz8b_hedefler", "Faz 7 hedefleri bu sette", ["Hedef", "Eşik", "Değer", "Sonuç"],
              [[e, target_cell(PHASE8B_TUNED_NEW, h, "esik", "sayi"), target_cell(PHASE8B_TUNED_NEW, h, "deger", b), target_cell(PHASE8B_TUNED_NEW, h, "gecti", "gecti")] for e, h, b in
               TARGETS[:1] + [("Kapsam dışı kayıt (zorunlu)", "kapsam_disi_kayit", "sayi")] + TARGETS[1:4] + TARGETS[7:]]),
        table("faz8b_kimlik_gosterilen", "Kimlik ölçümü: gösterilen cevaplardaki numaralar", ["Tür", "Yeni düzen", "Eski düzen"],
              [[e] + [(PHASE8B_IDENTIFIER_ANALYSIS, f"gosterilen.{d}.{k}#", "sayi") for d in ("yeni", "eski")] for e, k in [
                  ("Uydurma", "uydurma"), ("Yanlış kayıt", "yanlis_kayit"), ("Doğru numara", "dogru"), ("Numarasız", "numarasiz")]]),
        table("faz8b_kimlik", "Kimlik ölçümü: uydurma kimlik kuralı", ["Ölçü", "Değer"], [
            ["Soru", (PHASE8B_IDENTIFIER_ANALYSIS, "soru_sayisi", "sayi")], ["Taslak", (PHASE8B_IDENTIFIER_ANALYSIS, "taslak_sayisi", "sayi")],
            ["Desteksiz numaralı taslak", (PHASE8B_IDENTIFIER_ANALYSIS, "desteksiz_taslak.adet", "sayi")],
            ["Eski kuralların durdurduğu", (PHASE8B_IDENTIFIER_ANALYSIS, "desteksiz_taslak.durduran.eski_kurallar", "sayi")],
            ["Durdurulmayan", (PHASE8B_IDENTIFIER_ANALYSIS, "desteksiz_taslak.durduran.durdurulmadi", "sayi")],
            ["Yeni kuralın tek başına katkısı", (PHASE8B_IDENTIFIER_ANALYSIS, "yeni_kuralin_katkisi", "metin")],
            ["Yeni kuralın yanlış bayrağı", (PHASE8B_IDENTIFIER_ANALYSIS, "yanlis_bayrak#", "sayi")],
            ["Doğru numara sorularında doğru (yeni düzen)", (PHASE8B_IDENTIFIER_ANALYSIS, ["D.yeni_dogru", "D.soru"], "kesir")],
            ["Doğru numara sorularında doğru (eski düzen)", (PHASE8B_IDENTIFIER_ANALYSIS, ["D.eski_dogru", "D.soru"], "kesir")],
            ["Eğitim ↔ yeni set en yakın kalıp benzerliği", (PHASE8B_QUESTION_SET, "denetimler.sizinti.en_yakin_kalip_benzerligi", "oran")],
            ["Eğitim ↔ yeni set en yakın soru benzerliği", (PHASE8B_QUESTION_SET, "denetimler.sizinti.en_yakin_soru_benzerligi", "oran")]]),
    ]),
    # ---- Phase 9: final = faz9_karsilastirma.json (output of scripts/phase09_analysis.py) and faz9_boyut.json ------------
    ("Faz 9 · Araç deneyi (elle araçlar ↔ otomatik üretilen araçlar)", [
        table("faz9_kollar", "Üç kol, aynı sorular", ["Ölçü", "Elle araçlar", "Otomatik araçlar", "Elle + iki düzeltme (keşif)"],
              [[e] + [(PHASE9_COMPARISON, [f"kollar.{k}.{y}" for y in paths] if len(paths) > 1 else f"kollar.{k}.{paths[0]}", b) for k in ARMS] for e, paths, b in [
                  ("Tüm set, gösterilen doğru", ("tum_set.dogru", "tum_set.n"), "kesir"),
                  ("Doğrudan sınıflar (tek_cagri + zincir), gösterilen doğru", ("dogrudan.dogru", "dogrudan.n"), "kesir"),
                  ("Gösterilen yanlış (ön kayıttaki tanım)", ("tum_set.gosterilen_yanlis",), "sayi"),
                  ("baglam_siniri ile biten", ("tum_set.baglam_siniri",), "sayi"),
                  ("sure_siniri ile biten", ("tum_set.durumlar.sure_siniri",), "sayac"),
                  ("Süre medyanı / p90 (sn)", ("tum_set.sure_medyan_s", "tum_set.sure_p90_s"), "bolu"),
                  ("İstem tokenı medyanı / en büyük", ("tum_set.istem_medyan", "tum_set.istem_en_cok"), "bolu"),
                  ("Sızıntı", ("sizinti#",), "sayi")]]
              + [["Kâhin tavanı", (PHASE9_COMPARISON, "kahin_tavanlari.el", "metin"), (PHASE9_COMPARISON, "kahin_tavanlari.otomatik", "metin"), None]],
              "Üçüncü kol keşif amaçlıdır: düzeltmeler Faz 8b sonuçlarına bakılarak seçildi, p değeri yoktur."),
        table("faz9_siniflar", "Soru sınıflarına göre gösterilen doğru", ["Sınıf", "Soru", "Elle", "Otomatik", "Elle + düzeltme"],
              [[s, (PHASE9_COMPARISON, f"kollar.el.siniflar.{s}.n", "sayi")] + [(PHASE9_COMPARISON, f"kollar.{k}.siniflar.{s}.dogru", "sayi") for k in ARMS]
               for s in ("tek_cagri", "zincir", "engelli", "toplama", "yok")]),
        table("faz9_birincil", "Birincil ölçü: doğrudan sınıflarda eşli karşılaştırma", ["Ölçü", "Değer"], [
            ["Soru", (PHASE9_COMPARISON, "birincil.n", "sayi")], ["İkisi de doğru", (PHASE9_COMPARISON, "birincil.ikisi_dogru", "sayi")],
            ["Yalnızca elle doğru", (PHASE9_COMPARISON, "birincil.yalniz_el#", "sayi")], ["Yalnızca otomatik doğru", (PHASE9_COMPARISON, "birincil.yalniz_otomatik#", "sayi")],
            ["İkisi de yanlış", (PHASE9_COMPARISON, "birincil.ikisi_yanlis", "sayi")], ["Kesin McNemar p (iki yönlü)", (PHASE9_COMPARISON, "birincil.mcnemar_p", "p")],
            ["Ön kayıtlı karar", (PHASE9_COMPARISON, "birincil.karar", "metin")],
            ["Bir çift ters dönseydi p", (PHASE9_COMPARISON, "birincil.duyarlilik.bir_cift_donerse_p", "p")],
            ["Doğrulayıcı kör nokta soruları çıkarılırsa p", (PHASE9_COMPARISON, "birincil.duyarlilik.dogrulayici_kor_noktasi_haric_p", "p")],
            ["Negatif kontrol: yakalanan", (PHASE9_COMPARISON, "negatif_kontrol_yakalanan#", "sayi")],
            ["Üçüncü kol: yalnızca elle doğru", (PHASE9_COMPARISON, "ucuncu_kol.yalniz_el#", "sayi")],
            ["Üçüncü kol: yalnızca düzeltmeli doğru", (PHASE9_COMPARISON, "ucuncu_kol.yalniz_duzeltme#", "sayi")]]),
        table("faz9_boyut", "Araç listesinin bağlamdaki yeri", ["Ölçü", "Elle araçlar", "Otomatik araçlar"], [
            [e] + [(PHASE9_SIZE, f"{k}.{y}", "sayi") for k in ("el", "otomatik")] for e, y in [
                ("Araç sayısı", "arac_sayisi"), ("Karakter", "karakter"), ("Token", "token"), ("Soru ve sonuçlara kalan token", "kalan")]]),
    ]),
    # ---- After Phase 11: kor_nokta_olcum.json (scripts/verifier_blind_spots.py; recorded drafts, the model was not run) ---
    ("Faz 11'den sonra · Doğrulayıcının iki kör noktası (kayıtlı taslaklarla)", [
        table("kor_nokta", "Eski ve yeni doğrulayıcı, aynı kayıtlı taslaklar", ["Ölçü", "Değer"], [
            [e, (BLIND_SPOT_MEASURE, f"model_toplami.{a}", "sayi")] for e, a in [
                ("Model koşularındaki taslak", "taslak"), ("Ret → kabul, beklenen değeri taşıyor", "ret_kabul_dogru"),
                ("Ret → kabul, beklenen değeri taşımıyor", "ret_kabul_eksik"),
                ("Kabul → ret, beklenen değeri taşıyor", "kabul_ret_dogru"),
                ("Kabul → ret, beklenen değeri taşımıyor", "kabul_ret_eksik"),
                ("Gösterilen cevabı doğruya dönen soru", "kazanilan_dogru_cevap"),
                ("Doğru cevabı kaybedilen soru", "kaybedilen_dogru_cevap"),
                ("Beklenen değeri taşımayan yeni gösterilen cevap", "gosterilen_yeni_eksik")]]
            + [["Kâhin koşularında kabul → ret", (BLIND_SPOT_MEASURE, "kontrol_ozeti.kahin_kabul_ret", "sayi")],
               ["Uydurma kontrollerinde ret → kabul", (BLIND_SPOT_MEASURE, "kontrol_ozeti.uydurma_ret_kabul", "sayi")]],
            "Sonradan yapılan ölçüm: kayıtlı taslaklar yeniden oynatıldı, model çalıştırılmadı; fazların ön kayıtlı sonuçları "
            "değişmez. Beklenen değeri taşımayan cevaplar elle okundu (README)."),
    ]),
]

# --- "Sonuçlar ve sınırlamalar" (conclusions and limitations): every number in a template comes from a manifest cell (no hand-written number) ---
CONCLUSIONS = [
    ("Yetkisiz erişimde sızıntı yok: Faz 3 kapı denemelerinde {a} sızıntı ({n} denemede); Faz 7 saklanan sette {b}, Faz 8 ince ayarlı modelde {c}, "
     "Faz 8b tüm şirket sorularında {d}. Faz 7 saklanan sette denemelerin {k} tanesi kapıya ulaştı; kalanlar kapıyı değil modelin kendi reddini ölçer.",
     {"a": (PHASE3_TOOLS, "denemeler.sizinti", "sayi"), "n": (PHASE3_TOOLS, "denemeler.toplam", "sayi"), "b": target_cell(PHASE7_HELD_OUT, "yetkisiz_sizinti", "deger", "sayi"),
      "c": target_cell(PHASE8_TUNED, "yetkisiz_sizinti", "deger", "sayi"), "d": target_cell(PHASE8B_TUNED_NEW, "yetkisiz_sizinti", "deger", "sayi"), "k": (PHASE7_HELD_OUT, "ozet.yetkisiz.kapiya_ulasti", "sayi")}),
    ("Belge arama: ayrılmış sette anlam araması ilk 5 sonuçta {a}, kabul yöntemi (kelime + anlam + sıralayıcı) {b} isabet verdi; kelime araması tek başına {c}. "
     "Belgeleri ve soruları aynı kişi yazdı, gerçek belgeler daha zor olabilir.",
     {"a": (PHASE5_SEARCH, "yontemler.anlam.ayrilmis.isabet@5", "oran"), "b": (PHASE5_SEARCH, "yontemler.karma_sirala.ayrilmis.isabet@5", "oran"),
      "c": (PHASE5_SEARCH, "yontemler.bm25.ayrilmis.isabet@5", "oran")}),
    ("İzleme kuralları: üç tohumda geri çağırmanın en düşüğü {a}, aylık yanlış alarmın en yükseği {b} (üst sınır {c}); olayları ve kuralları aynı kişi yazdığı "
     "için bu oran gerçek dünyadaki başarıyı göstermez.",
     {"a": (PHASE4_MONITORING, [f"setler.tohum{t}.olayli.puan.geri_cagirma" for t in (42, 43, 44)], "en_az_oran"),
      "b": (PHASE4_MONITORING, [f"setler.tohum{t}.temiz.yanlis_alarm.aylik" for t in (42, 43, 44)], "en_cok_oran"), "c": (PHASE4_MONITORING, "kabul.aylik_yanlis_alarm_en_cok", "sayi")}),
    ("Faz 7 ilk ölçümünde (saklanan set) doğrulanmış yanlış sayı {a} (sınır {b}), belge cevabı {c} ve geçerli alıntı {d} hedefi tutmadı; araç seçimi {e}, rakam doğruluğu {f} geçti.",
     {"a": target_cell(PHASE7_HELD_OUT, "dogrulanmis_yanlis_sayi", "deger", "oran"), "b": target_cell(PHASE7_HELD_OUT, "dogrulanmis_yanlis_sayi", "esik", "sayi"),
      "c": target_cell(PHASE7_HELD_OUT, "belge_cevap", "deger", "oran"), "d": target_cell(PHASE7_HELD_OUT, "belge_alinti", "deger", "oran"),
      "e": target_cell(PHASE7_HELD_OUT, "arac_secimi", "deger", "oran"), "f": target_cell(PHASE7_HELD_OUT, "rakam_dogrulugu", "deger", "oran")}),
    ("İnce ayarda belge cevabı {a} → {b}, araç seçimi {c} → {d} oldu; ama bu davranışlar Faz 7 saklanan seti okunduktan sonra bilerek öğretildi, yeni soru türüne "
     "genelleşme sayılmaz. Doğrulanmış yanlış sayı {e} ile sınırın ({f}) üstünde kaldı; rakam doğruluğundaki artışın p değeri {g}, anlamlı değil.",
     {"a": target_cell(PHASE8_BASE, "belge_cevap", "deger", "oran"), "b": target_cell(PHASE8_TUNED, "belge_cevap", "deger", "oran"),
      "c": target_cell(PHASE8_BASE, "arac_secimi", "deger", "oran"), "d": target_cell(PHASE8_TUNED, "arac_secimi", "deger", "oran"),
      "e": target_cell(PHASE8_TUNED, "dogrulanmis_yanlis_sayi", "deger", "oran"), "f": target_cell(PHASE8_TUNED, "dogrulanmis_yanlis_sayi", "esik", "sayi"),
      "g": (PHASE8_COMPARISON, "eslestirme.bolum.arac.metrikler.cevap_dogrulugu.p", "p")}),
    ("Genel Türkçe korundu: Belebele doğruluğu kendi temel modelde {a}, ince ayarlıda {b}; fark {c} puan (yalnız ilki doğru {d}, yalnız ikincisi doğru {e}, p = {f}).",
     {"a": (PHASE8_FORGETTING["ince"], "karsilastirma.temel_dogruluk", "sayi"), "b": (PHASE8_FORGETTING["ince"], "karsilastirma.ayarli_dogruluk", "sayi"),
      "c": (PHASE8_FORGETTING["ince"], "karsilastirma.fark", "sayi"), "d": (PHASE8_FORGETTING["ince"], "karsilastirma.yalniz_temel_dogru", "sayi"),
      "e": (PHASE8_FORGETTING["ince"], "karsilastirma.yalniz_ayarli_dogru", "sayi"), "f": (PHASE8_FORGETTING["ince"], "karsilastirma.p", "p")}),
    ("Faz 8b sahiplik kuralı müşteriye özel sorularda gösterilen doğrulanmış yanlışı {a} → {b} indirdi; ama düzeltme turları süre medyanını {c} → {d} sn çıkardı "
     "(oran {e}) ve süre ölçütü tutmadı. Kazanım tek şablondan ve tek hata biçiminden geliyor, tek koşu.",
     {"a": (PHASE8B_ANALYSIS, "karar.musteri_yanlis_azalir.eski", "sayi"), "b": (PHASE8B_ANALYSIS, "karar.musteri_yanlis_azalir.yeni", "sayi"),
      "c": (PHASE8B_ANALYSIS, "karar.sure_medyan_artisi.eski_s", "sayi"), "d": (PHASE8B_ANALYSIS, "karar.sure_medyan_artisi.yeni_s", "sayi"),
      "e": (PHASE8B_ANALYSIS, "karar.sure_medyan_artisi.oran", "oran")}),
    ("Uydurma kimlik kuralı {n} soruluk zorlayıcı sette {k} desteksiz taslaktan hiçbirini tek başına durdurmadı (dosyadaki ifade: \"{m}\"). Asıl açık uydurma değil yanlış kayıt: "
     "gösterilen yanlış kayıt yeni düzende {y}, eski düzende {e}. Bağımsız olay sayısı çok küçük.",
     {"n": (PHASE8B_IDENTIFIER_ANALYSIS, "soru_sayisi", "sayi"), "k": (PHASE8B_IDENTIFIER_ANALYSIS, "desteksiz_taslak.adet", "sayi"), "m": (PHASE8B_IDENTIFIER_ANALYSIS, "yeni_kuralin_katkisi", "metin"),
      "y": (PHASE8B_IDENTIFIER_ANALYSIS, "gosterilen.yeni.yanlis_kayit#", "sayi"), "e": (PHASE8B_IDENTIFIER_ANALYSIS, "gosterilen.eski.yanlis_kayit#", "sayi")}),
    ("Araç deneyi: aynı modelle elle araçlar {a} soruyu, otomatik üretilen araçlar {b} soruyu doğru cevapladı. Ön kayıtlı birincil testin p değeri {p}, doğrulayıcının "
     "kör noktası çıkarılınca {q}: yön net, anlamlılık kırılgan. Üçüncü kol yalnızca betimleyici. Bu kör nokta ve belgede yazıyla sayı "
     "sonradan kapatıldı: kayıtlı {t} taslakta {ra} doğru taslak artık geçiyor, {k} koşu–soru çiftinde gösterilen cevap doğruya dönüyor, "
     "{e} çiftte beklenen değeri taşımayan (sayıları doğru, eksik ya da soruyu karşılamayan) bir cevap gösterilirdi; kâhinde "
     "kabul → ret {c}, uydurma kontrollerinde ret → kabul {d}. Cevaptaki yazıyla sayılar hâlâ denetlenmiyor.",
     {"a": (PHASE9_COMPARISON, ["kollar.el.tum_set.dogru", "kollar.el.tum_set.n"], "kesir"), "b": (PHASE9_COMPARISON, ["kollar.otomatik.tum_set.dogru", "kollar.otomatik.tum_set.n"], "kesir"),
      "p": (PHASE9_COMPARISON, "birincil.mcnemar_p", "p"), "q": (PHASE9_COMPARISON, "birincil.duyarlilik.dogrulayici_kor_noktasi_haric_p", "p"),
      "t": (BLIND_SPOT_MEASURE, "model_toplami.taslak", "sayi"), "ra": (BLIND_SPOT_MEASURE, "model_toplami.ret_kabul_dogru", "sayi"),
      "k": (BLIND_SPOT_MEASURE, "model_toplami.kazanilan_dogru_cevap", "sayi"), "e": (BLIND_SPOT_MEASURE, "model_toplami.gosterilen_yeni_eksik", "sayi"),
      "c": (BLIND_SPOT_MEASURE, "kontrol_ozeti.kahin_kabul_ret", "sayi"),
      "d": (BLIND_SPOT_MEASURE, "kontrol_ozeti.uydurma_ret_kabul", "sayi")}),
    ("Hız sınırı: bu bilgisayarda (ekran kartsız) seçilen modelin üretim hızı saniyede {a} token; bu yüzden uzun cevaplar sürenin çoğunu alıyor ve süre sınırları "
     "buna göre konuldu. Bütün ölçümler tek makinede ve çoğunlukla tek koşuyla yapıldı.",
     {"a": (PHASE0_SPEED, "adaylar[model=qwen3.5:9b].uretim_tps", "iki")}),
]


class ManifestError(Exception):
    """A file or key in the manifest is missing."""


# --- Reading and formatting ----------------------------------------------------------------------------------------------
def _to_turkish_separators(text: str) -> str:
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _plain(x) -> str:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if isinstance(x, float) and not x.is_integer():
        return _to_turkish_separators(f"{x:,.4f}".rstrip("0").rstrip("."))
    return _to_turkish_separators(f"{int(x):,}") if abs(x) >= 1000 else str(int(x))


def _decimals(x, n: int) -> str:
    return _to_turkish_separators(f"{float(x):,.{n}f}")


def format_value(fmt: str, v) -> str:
    """Turns the raw value (in some formats the list of values) into report text."""
    if fmt == "kesir":
        return f"{_plain(v[0])}/{_plain(v[1])}"
    if fmt == "ok":
        return f"{_plain(v[0])} → {_plain(v[1])}"
    if fmt == "bolu":
        return f"{_plain(v[0])} / {_plain(v[1])}"
    if fmt in ("en_az_oran", "en_cok_oran"):
        return _decimals((min if fmt == "en_az_oran" else max)(v), 3)
    if v is None:
        return "–"
    if fmt == "evet_hayir":
        return "evet" if v else "hayır"
    if fmt == "gecti":
        return "Geçti" if v else "Kaldı"
    return {"sayi": _plain, "metin": str, "sayac": _plain, "oran": lambda x: _decimals(x, 3), "p": lambda x: _decimals(x, 4),
            "sn": lambda x: _decimals(x, 1), "iki": lambda x: _decimals(x, 2),
            "kurus_tl": lambda x: _decimals(int(x) / 100, 2)}[fmt](v)


def _lookup(data, path: str):
    """Follows a dotted path in the data; the part that is not found is reported with a KeyError."""
    want_length = path.endswith("#")
    current, followed_path = data, ""
    for part in re.findall(r"\[[^\]]*\]|[^.\[\]#]+", path):
        followed_path += part if part.startswith("[") else ("." + part if followed_path else part)
        try:
            if part.startswith("["):
                inner = part[1:-1]
                if "=" in inner:
                    field, value = inner.split("=", 1)
                    current = next(x for x in current if x[field] == value)
                elif inner.isdigit():
                    current = current[int(inner)]
                else:
                    current = current[inner]
            else:
                current = current[part]
        except (KeyError, IndexError, StopIteration, TypeError):
            raise KeyError(followed_path) from None
    return len(current) if want_length else current


class Reader:
    def __init__(self, directory: Path):
        self.directory, self.cache = directory, {}

    def load_file(self, name: str):
        if name not in self.cache:
            path = self.directory / name
            if not path.is_file():
                raise ManifestError(f"manifest dosyası yok: {name} (aranan yer: {path})")
            try:
                self.cache[name] = json.loads(path.read_text(encoding="utf-8"))
            except ValueError as e:
                raise ManifestError(f"manifest dosyası okunamadı: {name} ({e})") from None
        return self.cache[name]

    def deger(self, cell):
        """The raw value of a (file, path or list of paths, format) cell."""
        file, path, fmt = cell
        data = self.load_file(file)
        try:
            if isinstance(path, (list, tuple)):
                return [_lookup(data, y) for y in path]
            if fmt == "sayac":  # the parent dictionary is required; 0 only if just the last key is missing
                parent, _, last_key = path.rpartition(".")
                return _lookup(data, parent).get(last_key, 0)
            return _lookup(data, path)
        except KeyError as e:
            raise ManifestError(f"manifest anahtarı yok: {file} → {e.args[0]}") from None


def process_cell(reader: Reader, cell):
    """Turns a cell into a (raw value, display) pair; None is an empty cell."""
    if cell is None:
        return None, ""
    if isinstance(cell, str):
        return cell, cell
    raw = reader.deger(cell)
    return raw, format_value(cell[2], raw)


def cell_files(cell) -> list[str]:
    return [cell[0]] if isinstance(cell, tuple) and len(cell) == 3 and isinstance(cell[0], str) else []


def manifest_files() -> list[str]:
    """All the files the manifest reads (sorted, without repeats)."""
    cells = [h for _, ts in MANIFEST for t in ts for row in t["satirlar"] for h in row]
    cells += [h for _, hs in CONCLUSIONS for h in hs.values()]
    return sorted({d for h in cells for d in cell_files(h)})


def build_sections(reader: Reader) -> tuple[list[dict], list[dict], list[str]]:
    """Builds all the tables and conclusion items; it collects the missing ones and reports them at the end with a single error."""
    errors, sections, conclusions = [], [], []

    def safe_process(cell):
        try:
            return process_cell(reader, cell)
        except ManifestError as e:
            if str(e) not in errors:
                errors.append(str(e))
            return None, "?"

    for title, tables in MANIFEST:
        section_tables = []
        for t in tables:
            rows, sources = [], []
            for row in t["satirlar"]:
                processed = [safe_process(h) for h in row]
                for h in row:
                    sources += [d for d in cell_files(h) if d not in sources]
                rows.append({"ad": row[0], "degerler": [i[0] for i in processed[1:]], "gosterim": [i[1] for i in processed[1:]]})
            section_tables.append({"id": t["id"], "baslik": t["baslik"], "not": t["not"], "kolonlar": t["kolonlar"], "kaynaklar": sources, "satirlar": rows})
        sections.append({"baslik": title, "tablolar": section_tables})
    for template, cells in CONCLUSIONS:
        display = {name: safe_process(h)[1] for name, h in cells.items()}
        conclusions.append({"metin": template.format(**display), "kaynaklar": sorted({d for h in cells.values() for d in cell_files(h)})})
    if errors:
        raise ManifestError("\n".join(errors))
    return sections, conclusions, errors


# --- Output --------------------------------------------------------------------------------------------------------------
def _markdown_row(cells) -> str:
    return "| " + " | ".join(str(h).replace("|", "\\|") for h in cells) + " |"


def markdown(sections: list[dict], conclusions: list[dict]) -> str:
    s = ["# Sayman: ölçüm raporu", "",
         "Bu rapor `scripts/phase11_report.py` ile `reports/` altındaki ölçüm dosyalarından üretilir; hiçbir ölçüm yeniden çalıştırılmaz. "
         "Her tablonun altında okunan dosyalar yazılıdır; hangi tablonun hangi dosya ve anahtardan okunduğu betikteki MANIFEST'tedir. "
         "Tablolardaki ve sonuç maddelerindeki sayılar dosyalardan okunur.", ""]
    for b in sections:
        s += [f"## {b['baslik']}", ""]
        for t in b["tablolar"]:
            s += [f"### {t['baslik']}", ""]
            if t["not"]:
                s += [t["not"], ""]
            s += [_markdown_row(t["kolonlar"]), _markdown_row(["---"] * len(t["kolonlar"]))]
            s += [_markdown_row([r["ad"]] + r["gosterim"]) for r in t["satirlar"]]
            s += ["", "Kaynak: " + ", ".join(f"`{k}`" for k in t["kaynaklar"]), ""]
    s += ["## Sonuçlar ve sınırlamalar", ""] + [f"- {x['metin']}" for x in conclusions]
    return "\n".join(s) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Faz 11: ölçüm raporunu yeniden üretir.")
    ap.add_argument("--rapor-klasoru", type=Path, default=ROOT / "reports", help="ölçüm dosyalarının klasörü")
    ap.add_argument("--cikti", type=Path, default=ROOT / "reports" / "faz11_rapor_guncel.md", help="Markdown çıktısı (JSON yanına .json olarak yazılır)")
    a = ap.parse_args(argv)
    sys.stderr.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sections, conclusions, _ = build_sections(Reader(a.rapor_klasoru))
    except ManifestError as e:
        print(f"HATA: rapor üretilemedi, çıktı yazılmadı.\n{e}", file=sys.stderr)
        return 1
    a.cikti.parent.mkdir(parents=True, exist_ok=True)
    with open(a.cikti, "w", encoding="utf-8", newline="\n") as f:
        f.write(markdown(sections, conclusions))
    with open(a.cikti.with_suffix(".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"bolumler": sections, "sonuclar": conclusions}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{a.cikti} ve {a.cikti.with_suffix('.json')} yazıldı ({sum(len(b['tablolar']) for b in sections)} tablo, {len(conclusions)} sonuç maddesi).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
