"""Draft of the 15 read-only tool definitions given to the model in the Phase 0 speed measurement.

These definitions are the first form of the real tools (Phase 3). They are used to measure how fast and how accurately
the model works with a tool list of real size. None of them changes data or moves money.
"""

from __future__ import annotations

DATE_DESCRIPTION = "Tarih, YYYY-MM-DD biçiminde"


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required or []),
                "additionalProperties": False,
            },
        },
    }


def _date_field(description: str = DATE_DESCRIPTION) -> dict:
    return {"type": "string", "format": "date", "description": description}


def _choice_field(values: list[str], description: str) -> dict:
    return {"type": "string", "enum": values, "description": description}


def _integer_field(description: str) -> dict:
    return {"type": "integer", "description": description}


def _text_field(description: str) -> dict:
    return {"type": "string", "description": description}


TOOLS: list[dict] = [
    _tool(
        "nakit_pozisyonu",
        "Şirketin bankalardaki toplam nakit pozisyonunu banka ve para birimi bazında verir.",
        {
            "tarih": _date_field("Hangi günün pozisyonu, YYYY-MM-DD. Verilmezse bugün."),
            "para_birimi": _choice_field(["TRY", "USD", "EUR", "hepsi"], "Gösterilecek para birimi"),
        },
    ),
    _tool(
        "banka_hareketleri",
        "Belirli bir tarih aralığındaki banka hesap hareketlerini listeler.",
        {
            "baslangic": _date_field("Aralığın ilk günü, YYYY-MM-DD"),
            "bitis": _date_field("Aralığın son günü, YYYY-MM-DD"),
            "banka": _text_field("Banka adı. Verilmezse bütün bankalar."),
            "yon": _choice_field(["giris", "cikis", "hepsi"], "Para girişi mi çıkışı mı"),
        },
        ["baslangic", "bitis"],
    ),
    _tool(
        "alacak_yaslandirma",
        "Açık alacakları kaç gündür beklediklerine göre gruplar (0-30, 31-60, 61-90, 90+ gün).",
        {
            "tarih": _date_field("Hangi güne göre, YYYY-MM-DD. Verilmezse bugün."),
            "musteri_adi": _text_field("Tek bir müşteri için istenirse müşteri adı"),
        },
    ),
    _tool(
        "geciken_alacaklar",
        "Vadesi geçmiş ve tahsil edilmemiş alacakları, gecikme gününe göre listeler.",
        {
            "min_gun": _integer_field("En az kaç gün gecikmiş olanlar listelensin"),
            "en_fazla": _integer_field("Listelenecek en fazla kayıt sayısı"),
        },
        ["min_gun"],
    ),
    _tool(
        "musteri_ozeti",
        "Bir müşterinin açık bakiyesini, son ödemelerini ve ödeme alışkanlığını özetler.",
        {"musteri_adi": _text_field("Müşteri adı")},
        ["musteri_adi"],
    ),
    _tool(
        "dso_hesapla",
        "Alacakların ortalama kaç günde tahsil edildiğini (DSO) hesaplar.",
        {"donem": _choice_field(["son_30_gun", "son_90_gun", "son_12_ay"], "Hesaplama dönemi")},
        ["donem"],
    ),
    _tool(
        "yaklasan_odemeler",
        "Önümüzdeki günlerde vadesi gelecek tedarikçi ödemelerini, çekleri ve vergileri listeler.",
        {"gun_sayisi": _integer_field("Bugünden itibaren kaç günlük ödemeler")},
        ["gun_sayisi"],
    ),
    _tool(
        "nakit_akis_tahmini",
        "Gelecek haftalar için haftalık nakit giriş, çıkış ve bakiye tahmini yapar.",
        {
            "hafta_sayisi": _integer_field("Kaç haftalık tahmin"),
            "senaryo": _choice_field(["temel", "kotumser", "iyimser"], "Tahmin senaryosu"),
        },
        ["hafta_sayisi"],
    ),
    _tool(
        "kur_riski",
        "Döviz cinsinden alacak ve borçların net pozisyonunu ve kur değişiminin etkisini gösterir.",
        {"para_birimi": _choice_field(["USD", "EUR", "GBP"], "Hangi para birimi için")},
    ),
    _tool(
        "doviz_kuru",
        "TCMB döviz alış ve satış kurunu verir.",
        {
            "para_birimi": _choice_field(["USD", "EUR", "GBP"], "Hangi para biriminin kuru"),
            "tarih": _date_field("Hangi günün kuru, YYYY-MM-DD. Verilmezse bugün."),
        },
        ["para_birimi"],
    ),
    _tool(
        "cek_portfoyu",
        "Alınan çekleri durumuna ve vadesine göre listeler.",
        {
            "durum": _choice_field(["portfoyde", "tahsilde", "karsiliksiz", "hepsi"], "Çekin durumu"),
            "vade_gun": _integer_field("Vadesine en fazla kaç gün kalanlar"),
        },
    ),
    _tool(
        "pos_tahsilatlari",
        "Belirli bir tarih aralığında POS ve sanal POS ile yapılan tahsilatları özetler.",
        {
            "baslangic": _date_field("Aralığın ilk günü, YYYY-MM-DD"),
            "bitis": _date_field("Aralığın son günü, YYYY-MM-DD"),
        },
        ["baslangic", "bitis"],
    ),
    _tool(
        "fatura_ara",
        "Satış faturalarını numara, müşteri ya da duruma göre arar.",
        {
            "fatura_no": _text_field("Fatura numarası"),
            "musteri_adi": _text_field("Müşteri adı"),
            "durum": _choice_field(["acik", "kapali", "iptal", "hepsi"], "Faturanın durumu"),
        },
    ),
    _tool(
        "belge_ara",
        "Mevzuat, sözleşme ve şirket politikası belgelerinde arama yapar, ilgili paragrafları kaynağıyla döndürür.",
        {
            "sorgu": _text_field("Aranacak konu ya da soru"),
            "belge_turu": _choice_field(["mevzuat", "sozlesme", "politika", "hepsi"], "Hangi tür belgelerde aranacak"),
        },
        ["sorgu"],
    ),
    _tool(
        "uyarilari_listele",
        "İzleme sisteminin ürettiği açık uyarıları listeler.",
        {
            "onem": _choice_field(["yuksek", "orta", "dusuk", "hepsi"], "Uyarının önem derecesi"),
            "son_gun": _integer_field("Son kaç günün uyarıları"),
        },
    ),
]

TOOLS_BY_NAME: dict[str, dict] = {a["function"]["name"]: a for a in TOOLS}
