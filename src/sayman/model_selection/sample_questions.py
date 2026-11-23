"""The 5 sample questions used in the Phase 0 measurement.

For every question it is written which tool is expected to be called and (if any) which arguments should come. Four of
the five require arguments; so the "valid tool call" criterion tests not only the tool name but also the arguments.
"""

from __future__ import annotations

WARMUP_QUESTION = "Merhaba, bana hangi konularda yardımcı olabilirsin?"

SAMPLE_QUESTIONS: list[dict] = [
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
