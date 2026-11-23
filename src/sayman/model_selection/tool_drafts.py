"""Faz 0 hız ölçümünde modele verilen 15 salt okunur araç tanımının taslağı.

Bu tanımlar gerçek araçların (Faz 3) ilk hâlidir. Modelin gerçek boyutta bir araç
listesiyle ne kadar hızlı ve doğru çalıştığını ölçmek için kullanılır. Hiçbiri veri
değiştirmez, para hareketi yapmaz.
"""

from __future__ import annotations

TARIH_ACIKLAMA = "Tarih, YYYY-MM-DD biçiminde"


def _arac(ad: str, aciklama: str, ozellikler: dict, zorunlu: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": ad,
            "description": aciklama,
            "parameters": {
                "type": "object",
                "properties": ozellikler,
                "required": list(zorunlu or []),
                "additionalProperties": False,
            },
        },
    }


def _tarih(aciklama: str = TARIH_ACIKLAMA) -> dict:
    return {"type": "string", "format": "date", "description": aciklama}


def _secim(degerler: list[str], aciklama: str) -> dict:
    return {"type": "string", "enum": degerler, "description": aciklama}


def _tamsayi(aciklama: str) -> dict:
    return {"type": "integer", "description": aciklama}


def _metin(aciklama: str) -> dict:
    return {"type": "string", "description": aciklama}


ARACLAR: list[dict] = [
    _arac(
        "nakit_pozisyonu",
        "Şirketin bankalardaki toplam nakit pozisyonunu banka ve para birimi bazında verir.",
        {
            "tarih": _tarih("Hangi günün pozisyonu, YYYY-MM-DD. Verilmezse bugün."),
            "para_birimi": _secim(["TRY", "USD", "EUR", "hepsi"], "Gösterilecek para birimi"),
        },
    ),
    _arac(
        "banka_hareketleri",
        "Belirli bir tarih aralığındaki banka hesap hareketlerini listeler.",
        {
            "baslangic": _tarih("Aralığın ilk günü, YYYY-MM-DD"),
            "bitis": _tarih("Aralığın son günü, YYYY-MM-DD"),
            "banka": _metin("Banka adı. Verilmezse bütün bankalar."),
            "yon": _secim(["giris", "cikis", "hepsi"], "Para girişi mi çıkışı mı"),
        },
        ["baslangic", "bitis"],
    ),
    _arac(
        "alacak_yaslandirma",
        "Açık alacakları kaç gündür beklediklerine göre gruplar (0-30, 31-60, 61-90, 90+ gün).",
        {
            "tarih": _tarih("Hangi güne göre, YYYY-MM-DD. Verilmezse bugün."),
            "musteri_adi": _metin("Tek bir müşteri için istenirse müşteri adı"),
        },
    ),
    _arac(
        "geciken_alacaklar",
        "Vadesi geçmiş ve tahsil edilmemiş alacakları, gecikme gününe göre listeler.",
        {
            "min_gun": _tamsayi("En az kaç gün gecikmiş olanlar listelensin"),
            "en_fazla": _tamsayi("Listelenecek en fazla kayıt sayısı"),
        },
        ["min_gun"],
    ),
    _arac(
        "musteri_ozeti",
        "Bir müşterinin açık bakiyesini, son ödemelerini ve ödeme alışkanlığını özetler.",
        {"musteri_adi": _metin("Müşteri adı")},
        ["musteri_adi"],
    ),
    _arac(
        "dso_hesapla",
        "Alacakların ortalama kaç günde tahsil edildiğini (DSO) hesaplar.",
        {"donem": _secim(["son_30_gun", "son_90_gun", "son_12_ay"], "Hesaplama dönemi")},
        ["donem"],
    ),
    _arac(
        "yaklasan_odemeler",
        "Önümüzdeki günlerde vadesi gelecek tedarikçi ödemelerini, çekleri ve vergileri listeler.",
        {"gun_sayisi": _tamsayi("Bugünden itibaren kaç günlük ödemeler")},
        ["gun_sayisi"],
    ),
    _arac(
        "nakit_akis_tahmini",
        "Gelecek haftalar için haftalık nakit giriş, çıkış ve bakiye tahmini yapar.",
        {
            "hafta_sayisi": _tamsayi("Kaç haftalık tahmin"),
            "senaryo": _secim(["temel", "kotumser", "iyimser"], "Tahmin senaryosu"),
        },
        ["hafta_sayisi"],
    ),
    _arac(
        "kur_riski",
        "Döviz cinsinden alacak ve borçların net pozisyonunu ve kur değişiminin etkisini gösterir.",
        {"para_birimi": _secim(["USD", "EUR", "GBP"], "Hangi para birimi için")},
    ),
    _arac(
        "doviz_kuru",
        "TCMB döviz alış ve satış kurunu verir.",
        {
            "para_birimi": _secim(["USD", "EUR", "GBP"], "Hangi para biriminin kuru"),
            "tarih": _tarih("Hangi günün kuru, YYYY-MM-DD. Verilmezse bugün."),
        },
        ["para_birimi"],
    ),
    _arac(
        "cek_portfoyu",
        "Alınan çekleri durumuna ve vadesine göre listeler.",
        {
            "durum": _secim(["portfoyde", "tahsilde", "karsiliksiz", "hepsi"], "Çekin durumu"),
            "vade_gun": _tamsayi("Vadesine en fazla kaç gün kalanlar"),
        },
    ),
    _arac(
        "pos_tahsilatlari",
        "Belirli bir tarih aralığında POS ve sanal POS ile yapılan tahsilatları özetler.",
        {
            "baslangic": _tarih("Aralığın ilk günü, YYYY-MM-DD"),
            "bitis": _tarih("Aralığın son günü, YYYY-MM-DD"),
        },
        ["baslangic", "bitis"],
    ),
    _arac(
        "fatura_ara",
        "Satış faturalarını numara, müşteri ya da duruma göre arar.",
        {
            "fatura_no": _metin("Fatura numarası"),
            "musteri_adi": _metin("Müşteri adı"),
            "durum": _secim(["acik", "kapali", "iptal", "hepsi"], "Faturanın durumu"),
        },
    ),
    _arac(
        "belge_ara",
        "Mevzuat, sözleşme ve şirket politikası belgelerinde arama yapar, ilgili paragrafları kaynağıyla döndürür.",
        {
            "sorgu": _metin("Aranacak konu ya da soru"),
            "belge_turu": _secim(["mevzuat", "sozlesme", "politika", "hepsi"], "Hangi tür belgelerde aranacak"),
        },
        ["sorgu"],
    ),
    _arac(
        "uyarilari_listele",
        "İzleme sisteminin ürettiği açık uyarıları listeler.",
        {
            "onem": _secim(["yuksek", "orta", "dusuk", "hepsi"], "Uyarının önem derecesi"),
            "son_gun": _tamsayi("Son kaç günün uyarıları"),
        },
    ),
]

ARACLAR_ADA_GORE: dict[str, dict] = {a["function"]["name"]: a for a in ARACLAR}
