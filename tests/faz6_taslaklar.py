"""Faz 6 taslak seti: doğrulayıcının geçirmesi ve yakalaması gereken cevap taslakları.

Bu dosya doğrulayıcıdan önce yazıldı ve doğrulayıcı yazılırken değiştirilmedi. Araç sonuçları gerçek araçların
gerçek ambardaki çıktılarıdır (tests/faz6_arac_sonuclari.json, scripts/faz6_arac_ornekleri.py); belge araması
sonucu Faz 5 kütüphanesindeki maddelerin metniyle elle yazıldı (belge arama aracı henüz yoktu).

Her taslak: soru, bu turda yapılan çağrılar, model cevabı taslağı, beklenen karar ve yakalanması beklenen sayılar.
- "dogru" taslaklar geçmelidir: aynı sayının farklı yazılışları (tam, yuvarlanmış, kesilmiş, "milyon"), eksi
  işaretsiz çıkış, anahtar adından gelen dönem ("son 12 ay", "kur %10"), tarih, numaralı liste, fatura numarası.
- "yanlis" taslaklar yakalanmalıdır. "kaynaksiz": cevapta kaynağı bulunamaması beklenen sayıların metni.
- Bilinen kör nokta: doğru sayı yanlış müşteriye yazılırsa doğrulayıcı geçirir (KOR_NOKTA).
- Yorum kuralı: cevap Türkçe biçimde okunur. "55.865" gibi noktanın ardından tam üç rakam gelen yazılış binlik
  ayırıcı sayılır (55865); araçların "48.1745" biçimi (noktadan sonra üç rakam değil) ondalık sayılır.
"""

from __future__ import annotations

import json
from pathlib import Path

ARAC_SONUCLARI = Path(__file__).with_name("faz6_arac_sonuclari.json")

BELGE_CAGRILARI = {
    "belge_bereket": {
        "kullanici": "selin.aydin", "arac": "belge_ara", "argumanlar": {"sorgu": "Bereket İmalat ödeme vadesi"},
        "hata_mi": False,
        "yanit": {
            "cagri_id": "Cbelge0000001", "arac": "belge_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
            "sonuc": {"sorgu": "Bereket İmalat ödeme vadesi", "sonuclar": [{
                "parca_id": "SOZ-S001#m3", "belge": "Mal Alım Çerçeve Sözleşmesi — Bereket İmalat A.Ş.",
                "madde": "Ödeme Koşulları",
                "metin": "ŞİRKET, TEDARİKÇİ'nin düzenlediği faturaları fatura tarihinden itibaren 30 gün içinde "
                         "banka havalesiyle öder. Ödeme günü hafta sonuna denk gelirse izleyen ilk iş günü ödeme "
                         "yapılır."}]},
            "kaynak": {"belge_parcalari": ["SOZ-S001#m3"]}}},
}

DOGRU = [
    ("nakit_tam", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Bankalardaki toplam nakit 75.826.754,65 TL. En büyük hesap Banka A'daki TL hesabı: 74.205.083,37 TL."),
    ("nakit_yuvarlak", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Toplam nakit yaklaşık 75,8 milyon TL; bunun 74,2 milyon TL'si Banka A'da."),
    ("nakit_kesik", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Toplam nakit 75,82 milyon TL (75.826.754 TL)."),
    ("nakit_tamsayi", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Toplam nakit 75.826.755 TL."),
    ("doviz_hesaplari", "Döviz hesaplarımızda ne var?", ["nakit"],
     "EUR hesabında 17.904,83 EUR var; 55,8652 kurla 1.000.256,91 TL ediyor. USD hesabında 8.697,97 USD "
     "(419.020,36 TL)."),
    ("cikis_eksisiz", "Son bir haftada bankadan ne kadar para çıktı?", ["cikislar"],
     "25 Ağustos ile 1 Eylül 2026 arasında 41 çıkış hareketi oldu. TL hesaplarından toplam 12.766.257,63 TL çıktı; "
     "ayrıca 71.625,58 EUR ve 34.791,88 USD çıkış var."),
    ("cikis_eksili", "Son bir haftada bankadan ne kadar para çıktı?", ["cikislar"],
     "Toplam TL çıkışı -12.766.257,63 TL."),
    ("numarali_liste", "En büyük alacaklarımız hangi müşterilerde?", ["yaslandirma"],
     "En büyük 5 alacak:\n1. Green Home Store Ltd: 7.775.050,92 TL\n2. Prime Design House Ltd: 3.810.137,48 TL\n"
     "3. Bright Trading Co Pty Ltd: 2.830.371,80 TL\n4. Alpine Gifts Store BV: 2.586.733,95 TL\n"
     "5. Vadi Mavi Dekorasyon Ltd. Şti.: 2.543.589,02 TL\n"
     "Toplam açık alacak 75.580.245,68 TL; bunun 37.800.862,95 TL'si vadesi geçmiş."),
    ("fatura_kimlikleri", "Vadesini 30 günden fazla geçmiş faturalar hangileri?", ["geciken"],
     "Vadesini 30 günden fazla geçmiş 840 fatura var, toplamı 26.295.727,02 TL. En büyüğü F032047: Bright Trading "
     "Co Pty Ltd, 46 gün gecikmiş, açık tutar 30.508,53 USD (1.409.875,44 TL)."),
    ("dso", "Son 90 günün DSO'su kaç?", ["dso"],
     "Son 90 günlük DSO 50,93 gün, yani alacaklar ortalama yaklaşık 51 günde tahsil ediliyor. Açık alacak "
     "75.580.245,68 TL, dönem satışı 133.549.920,32 TL."),
    ("kur_yuzde", "Kur riskimiz ne durumda?", ["kur_riski"],
     "EUR pozisyonu 383.072,08 EUR (21.400.398,36 TL). Kur %10 değişirse etkisi 2.140.039,84 TL olur. USD tarafında "
     "kur %10 değişirse etki 516.379,24 TL."),
    ("doviz_kuru", "Dolar kuru kaç?", ["usd"],
     "1 Eylül 2026 tarihli TCMB alış kuruna göre 1 USD = 48,1745 TL."),
    ("doviz_kuru_arac_bicimi", "Dolar kuru kaç?", ["usd"],
     "USD alış kuru 48.1745 TL."),
    ("musteri_12_ay", "Green Home Store Ltd nasıl ödüyor?", ["musteri_a"],
     "Green Home Store Ltd'nin 71 açık faturası var; açık alacak 7.775.050,92 TL. Son 12 ayda kapanan 4 faturasının "
     "3'ü geç kapandı, ortalama gecikme 78 gün. En eski açık vade 19 Eylül 2025."),
    ("yaklasan", "Önümüzdeki 30 günde ne kadar ödememiz var?", ["yaklasan"],
     "Önümüzdeki 30 günde 22 tedarikçi faturası için toplam 43.510.100,56 TL ödeme var. Bunun 657 bin TL'si vadesi "
     "geçmiş: Orkide Kargo ve Lojistik A.Ş.'nin 657.272,69 TL'lik faturası 2 gün gecikti."),
    ("ret", "Bankada ne kadar paramız var?", ["satis_nakit_ret"],
     "Bu bilgiyi görme yetkiniz yok."),
    ("yil", "Green Home Store Ltd'nin en eski açık faturası ne zaman?", ["musteri_a"],
     "En eski açık faturanın vadesi 2025'te, 19 Eylül'de dolmuştu."),
    ("belge_alinti", "Bereket İmalat'a faturaları kaç günde ödüyoruz?", ["belge_bereket"],
     'Bereket İmalat faturalarını 30 gün içinde ödüyoruz: "fatura tarihinden itibaren 30 gün içinde banka '
     'havalesiyle öder" [SOZ-S001#m3]'),
]

YANLIS = [
    ("yazim_hatasi", "Green Home Store Ltd'nin geciken faturaları?", ["geciken"],
     "Green Home Store Ltd'nin F013945 numaralı faturası 347 gün gecikmiş; açık tutar 698.272,20 TL.",
     ["698.272,20"]),
    ("rakam_kaymasi", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Toplam nakit 75.862.754,65 TL.", ["75.862.754,65"]),
    ("uydurma_yuzde", "Green Home Store Ltd nasıl ödüyor?", ["musteri_a"],
     "Son 12 ayda kapanan 4 faturasının 3'ü geç kapandı; geç ödeme oranı %12.", ["%12"]),
    ("yanlis_yuvarlama", "Bankalarda toplam ne kadar nakitimiz var?", ["nakit"],
     "Toplam nakit yaklaşık 75,81 milyon TL.", ["75,81 milyon"]),
    ("yanlis_dso", "Son 90 günün DSO'su kaç?", ["dso"],
     "Son 90 günlük DSO 59,03 gün.", ["59,03"]),
    ("tur_uyusmazligi", "Green Home Store Ltd nasıl ödüyor?", ["musteri_a"],
     "Green Home Store Ltd'nin ödemeleri ortalama 71 gün gecikiyor.", ["71"]),
    ("yuzde_turu", "Vadesini 30 günden fazla geçmiş faturalar ne kadar?", ["geciken"],
     "Vadesini 30 günden fazla geçmiş faturalar, alacakların %30'u.", ["%30"]),
    ("turetilmis_toplam", "Kur riskimiz ne durumda?", ["kur_riski"],
     "EUR ve USD pozisyonlarının TL karşılığı toplam 26.564.190,74 TL.", ["26.564.190,74"]),
    ("reddedilen_cagri", "Bankada ne kadar paramız var?", ["satis_nakit_ret"],
     "Bankalarda toplam 75.826.754,65 TL var.", ["75.826.754,65"]),
    ("yanlis_tarih", "Green Home Store Ltd'nin en eski açık faturası ne zaman?", ["musteri_a"],
     "En eski açık vade 19 Eylül 2024.", ["19 Eylül 2024"]),
    ("belirsiz_bicim", "Euro kuru kaç?", ["nakit"],
     "EUR kuru 55.865 TL.", ["55.865"]),
    ("yanlis_alinti", "Bereket İmalat'a faturaları kaç günde ödüyoruz?", ["belge_bereket"],
     'Ödeme vadesi 45 gün: "fatura tarihinden itibaren 45 gün içinde banka havalesiyle öder" [SOZ-S001#m3]',
     ["45"]),
    ("getirilmeyen_parca", "Bereket İmalat'a geç ödersek ne olur?", ["belge_bereket"],
     'Gecikirsek aylık %2,25 faiz işler: "aylık %2,25 oranında temerrüt faizi" [SOZ-S001#m5]', ["%2,25"]),
]
# Alıntısı geçersiz olması beklenen taslaklar (sayılara ek olarak)
ALINTISI_GECERSIZ = {"yanlis_alinti", "getirilmeyen_parca"}

# Bilinen kör nokta: Green Home Store Ltd'nin açık alacağı Prime Design House Ltd'ye yazıldı. Sayı bu turdaki
# bir sonuçta (musteri_a, acik_alacak_tl) geçtiği için doğrulayıcı geçirir.
KOR_NOKTA = ("Prime Design House Ltd'nin açık alacağı ne kadar?", ["musteri_a", "musteri_b"],
             "Prime Design House Ltd'nin açık alacağı 7.775.050,92 TL.", ("musteri_a", "acik_alacak_tl"))


def arac_sonuclari() -> dict:
    veri = json.loads(ARAC_SONUCLARI.read_text(encoding="utf-8"))
    return {"bugun": veri["bugun"], "cagrilar": {**veri["cagrilar"], **BELGE_CAGRILARI}}
