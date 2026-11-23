"""Faz 0 ölçümünde kullanılan 5 örnek soru.

Her soru için hangi aracın çağrılması beklendiği ve (varsa) hangi argümanların
gelmesi gerektiği yazılıdır. Beşten dördü argüman ister; böylece "geçerli araç
çağrısı" ölçütü sadece araç adını değil argümanları da sınar.
"""

from __future__ import annotations

ISINMA_SORUSU = "Merhaba, bana hangi konularda yardımcı olabilirsin?"

ORNEK_SORULAR: list[dict] = [
    {
        "kimlik": "s1_nakit",
        "soru": "Şu an bankalarda toplam ne kadar nakitimiz var?",
        "beklenen_arac": "nakit_pozisyonu",
        "beklenen_argumanlar": {},
    },
    {
        "kimlik": "s2_geciken",
        "soru": "60 günden fazla gecikmiş alacakları listeler misin?",
        "beklenen_arac": "geciken_alacaklar",
        "beklenen_argumanlar": {"min_gun": 60},
    },
    {
        "kimlik": "s3_odemeler",
        "soru": "Önümüzdeki 14 gün içinde hangi ödemelerimiz var?",
        "beklenen_arac": "yaklasan_odemeler",
        "beklenen_argumanlar": {"gun_sayisi": 14},
    },
    {
        "kimlik": "s4_kur",
        "soru": "Bugün dolar kuru kaç?",
        "beklenen_arac": "doviz_kuru",
        "beklenen_argumanlar": {"para_birimi": "USD"},
    },
    {
        "kimlik": "s5_belge",
        "soru": "Tedarikçi sözleşmelerimizde gecikme cezası maddesi ne diyor?",
        "beklenen_arac": "belge_ara",
        "beklenen_argumanlar": {"belge_turu": "sozlesme"},
    },
]
