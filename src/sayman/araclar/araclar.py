"""Modelin çağırdığı 14 salt okunur araç.

Her araç Faz 2 metriklerini ya da sabit, parametreli SELECT sorgularını kullanır; hiçbiri yazmaz. Araçlar
yetkiyi bilmez: kendisine verilen (kapsamı daraltılmış, salt okunur) bağlantıda çalışır. Kimlik, yetki,
tarih kontrolü ve denetim kaydı sunucunun tek kapısında yapılır (sunucu.py).

Faz 6'da eklenen belge_ara ambara değil Faz 5 belge kütüphanesine bakar. Belgelerde kapsam bir bağlantı değil
roldür: arayıcı, rolün göremediği parçayı sıralamaya hiç almaz. Varsayılan yöntem anlam aramasıdır; sıralayıcı
("karma_sirala") sunucunun bir ayarıdır.

Çıktı biçimi:
- Tutarlar metin olarak, nokta ondalıkla ve iki haneyle: "-1234.56". TL alanlarının adı "_tl" ile biter;
  döviz tutarlarının yanında "para_birimi" vardır. Kayan noktalı sayı kullanılmaz.
- Listeler en çok `en_fazla` (1-10, varsayılan 5) satırdır; kesilen listelerde toplam adet ve toplam tutar her
  zaman verilir.
- "kaynak": gösterilen satırların kimlikleri (tablo -> kimlik listesi). Doğrulayıcı (Faz 6) cevaptaki sayıları
  bunlarla eşleştirir. Yalnızca toplam veren dört araçta (TOPLU_ARACLAR) kaynak boştur: bu cevaplar sunucunun
  verdiği cagri_id ile anılır; denetim kaydında araç, argümanlar, rol ve simülasyon günü (simdi) durduğu için
  aynı çağrı yeniden çalıştırılıp aynı sonuç alınabilir.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from sayman.ambar import metrikler
from sayman.ambar.kurulum import satirlar

# Liste boyu ölçüme göre: qwen3.5:9b'de 20 satırlık bir fatura listesi 3.390 token tuttu (8.192'lik pencereye araç
# listesiyle birlikte yalnızca bir tane sığıyordu). 10 satır ~1.750 token. Toplam adet ve tutar her zaman tam verilir.
EN_FAZLA = {"type": "integer", "minimum": 1, "maximum": 10, "description": "En fazla kaç satır listelensin (varsayılan 5)"}
TARIH = {"type": "string", "format": "date", "description": "Hangi gün itibarıyla, YYYY-MM-DD. Verilmezse bugün."}
AD = {"type": "string", "maxLength": 100}
KIMLIK = {"type": "string", "maxLength": 40}
DONEMLER = {"son_30_gun": 30, "son_90_gun": 90, "son_12_ay": 365}
# Belge araması: madde metinlerinin medyanı 277, %90'ı 886, %99'u 2.249 karakter (Faz 5 kütüphanesi, 3.003 parça;
# sözleşme ve politika maddeleri en çok 199 karakter, uzunlar kanun maddeleri). 2.500 karakterden uzun madde (%0,8)
# kelime sınırında kesilir. Önce 1.200 denendi: %5,1 kesiliyordu ve duman testinde TTK md. 82'nin "on yıl" ifadesi
# (1.413. karakter) kesilip atıldı. En çok 3 sonuç ≈ 7.500 karakter; Faz 6 ölçümünde madde metni ~3,1 karakter/token,
# yani ~2.400 token: araç listesi (~2.340) ve sistem istemiyle birlikte 8.192'lik pencereye sığar.
BELGE_VARSAYILAN = 3
BELGE_EN_FAZLA = 3
BELGE_METIN_SINIRI = 2500
CEK_DURUMLARI = {"bekleyen": ("portfoyde", "tahsilde"), "portfoyde": ("portfoyde",), "tahsilde": ("tahsilde",),
                 "karsiliksiz": ("karsiliksiz",), "tahsil_edildi": ("tahsil_edildi",),
                 "hepsi": ("portfoyde", "tahsilde", "karsiliksiz", "tahsil_edildi")}


class AracHatasi(Exception):
    """Kullanıcının düzeltebileceği bir girdi hatası (ör. başlangıç bitişten sonra)."""


class AracKullanilamaz(Exception):
    """Araç bu kurulumda çalışamıyor (ör. belge kütüphanesi yüklenmemiş)."""


@dataclass
class Baglam:
    con: object
    simdi: dt.date
    rol: str | None = None          # yalnızca belge_ara kullanır: belgelerde kapsam bağlantı değil roldür
    belge: object | None = None     # belge arayıcısı (sayman.belge.arama.Arayici ya da aynı ara() yöntemi olan)
    belge_yontemi: str = "anlam"


@dataclass(frozen=True)
class Arac:
    ad: str
    aciklama: str
    ozellikler: dict
    calistir: Callable[[Baglam, dict], tuple[dt.date, dict, dict]] = field(repr=False)
    zorunlu: tuple[str, ...] = ()

    @property
    def sema(self) -> dict:
        return {"type": "object", "properties": self.ozellikler, "required": list(self.zorunlu),
                "additionalProperties": False}

    @property
    def tarih_alanlari(self) -> tuple[str, ...]:
        return tuple(a for a, s in self.ozellikler.items() if s.get("format") == "date")


# ---------------------------------------------------------------- yardımcılar


def tl(kurus) -> str:
    k = int(kurus)
    return f"{'-' if k < 0 else ''}{abs(k) // 100}.{abs(k) % 100:02d}"


def kur_metni(kur) -> str:
    return format(Decimal(kur).normalize(), "f")


def _gun(b: Baglam, a: dict, alan: str = "tarih") -> dt.date:
    return dt.date.fromisoformat(a[alan]) if a.get(alan) else b.simdi


def _en_fazla(a: dict) -> int:
    return a.get("en_fazla", 5)


def _normal(metin: str) -> str:
    return " ".join(metin.replace("İ", "i").replace("I", "ı").lower().split())


def musteri_bul(b: Baglam, ad: str, gun: dt.date) -> list[dict]:
    """Ada (ya da müşteri kimliğine) göre müşteri: önce tam eşleşme, yoksa adın içinde geçenler (en az 3 harf).
    Yalnızca o güne kadar işlemi olmuş ve bağlantının kapsamındaki müşteriler aranır."""
    aranan = _normal(ad)
    adaylar = satirlar(b.con, "SELECT musteri_id, ad, ulke, para_birimi, vade_gun FROM musteriler "
                              "WHERE ilk_islem_tarihi <= $d ORDER BY ad, musteri_id", {"d": gun})
    tam = [m for m in adaylar if _normal(m["ad"]) == aranan or m["musteri_id"].lower() == aranan]
    if tam:
        return tam
    if len(aranan) < 3:
        raise AracHatasi("Müşteri adı en az 3 harf olmalı.")
    return [m for m in adaylar if aranan in _normal(m["ad"])]


def _musteri_sonucu(eslesen: list[dict]):
    """Tek müşteri bulunduysa None; değilse modele dönecek açıklama."""
    if not eslesen:
        return {"bulunamadi": True, "mesaj": "Bu adla bir müşteri bulunamadı."}
    if len(eslesen) > 1:
        return {"birden_fazla": True, "aday_sayisi": len(eslesen), "adaylar": [m["ad"] for m in eslesen[:5]],
                "mesaj": "Birden fazla müşteri eşleşti; müşterinin tam adını yazın."}
    return None


def _dilimler(satir: dict | None) -> dict:
    alanlar = [f"{ad}_kurus" for ad, _, _ in metrikler.DILIMLER] + ["eslesmemis_tahsilat_kurus"]
    return {a.removesuffix("_kurus"): tl((satir or {}).get(a, 0)) for a in alanlar}


def _vadesi_gecmis(satir: dict) -> int:
    return sum(satir[f"{ad}_kurus"] for ad, alt, _ in metrikler.DILIMLER if alt is not None)


# ---------------------------------------------------------------- araçlar


def _nakit_pozisyonu(b, a):
    d = _gun(b, a)
    m = metrikler.nakit_pozisyonu(b.con, d)
    hesaplar = [{"hesap_id": h["hesap_id"], "banka": h["banka"], "para_birimi": h["para_birimi"],
                 "bakiye": tl(h["bakiye_kurus"]), "kur": kur_metni(h["kur"]), "bakiye_tl": tl(h["bakiye_try_kurus"])}
                for h in m["hesaplar"]]
    return d, {"hesaplar": hesaplar, "toplam_tl": tl(m["toplam_try_kurus"])}, \
        {"banka_hesaplari": [h["hesap_id"] for h in m["hesaplar"]]}


def _banka_hareketleri(b, a):
    bas, bit = _gun(b, a, "baslangic"), _gun(b, a, "bitis")
    if bas > bit:
        raise AracHatasi("Başlangıç günü bitiş gününden sonra olamaz.")
    kosul = ["h.tarih BETWEEN $bas AND $bit"]
    p = {"bas": bas, "bit": bit}
    if a.get("hesap_id"):
        kosul.append("h.hesap_id = $hesap")
        p["hesap"] = a["hesap_id"]
    yon = a.get("yon", "hepsi")
    if yon == "giris":
        kosul.append("h.tutar_kurus > 0")
    elif yon == "cikis":
        kosul.append("h.tutar_kurus < 0")
    nerede = " AND ".join(kosul)
    ozet = satirlar(b.con, f"""
        SELECT k.para_birimi, count(*) AS adet, sum(greatest(h.tutar_kurus, 0)) AS giris,
               sum(least(h.tutar_kurus, 0)) AS cikis
        FROM banka_hareketleri h JOIN banka_hesaplari k ON k.hesap_id = h.hesap_id
        WHERE {nerede} GROUP BY k.para_birimi ORDER BY k.para_birimi""", p)
    liste = satirlar(b.con, f"""
        SELECT h.hareket_id, h.hesap_id, k.para_birimi, h.tarih, h.tutar_kurus, h.aciklama
        FROM banka_hareketleri h JOIN banka_hesaplari k ON k.hesap_id = h.hesap_id
        WHERE {nerede} ORDER BY h.tarih DESC, h.hareket_id DESC LIMIT $n""", {**p, "n": _en_fazla(a)})
    sonuc = {
        "baslangic": bas.isoformat(), "bitis": bit.isoformat(),
        "hareket_sayisi": sum(int(o["adet"]) for o in ozet),
        "toplamlar": [{"para_birimi": o["para_birimi"], "adet": int(o["adet"]), "giris": tl(o["giris"]),
                       "cikis": tl(o["cikis"])} for o in ozet],
        "gosterilen": len(liste),
        "hareketler": [{"hareket_id": h["hareket_id"], "hesap_id": h["hesap_id"], "tarih": h["tarih"].isoformat(),
                        "tutar": tl(h["tutar_kurus"]), "para_birimi": h["para_birimi"], "aciklama": h["aciklama"]}
                       for h in liste],
    }
    return bit, sonuc, {"banka_hareketleri": [h["hareket_id"] for h in liste]}


def _alacak_yaslandirma(b, a):
    d = _gun(b, a)
    y = metrikler.yaslandirma(b.con, d)
    if a.get("musteri_adi"):
        eslesen = musteri_bul(b, a["musteri_adi"], d)
        aciklama = _musteri_sonucu(eslesen)
        if aciklama:
            return d, aciklama, {}
        m = eslesen[0]
        satir = next((s for s in y["musteriler"] if s["musteri_id"] == m["musteri_id"]), None)
        return d, {"musteri_id": m["musteri_id"], "musteri": m["ad"], "dilimler": _dilimler(satir),
                   "toplam_tl": tl(satir["toplam_kurus"] if satir else 0)}, {"musteriler": [m["musteri_id"]]}
    en_buyuk = y["musteriler"][:_en_fazla(a)]
    sonuc = {
        "dilimler": _dilimler(y["toplam"]), "toplam_tl": tl(y["toplam"]["toplam_kurus"]),
        "vadesi_gecmis_tl": tl(_vadesi_gecmis(y["toplam"])), "musteri_sayisi": len(y["musteriler"]),
        "en_buyuk_alacaklar": [{"musteri_id": s["musteri_id"], "musteri": s["ad"], "toplam_tl": tl(s["toplam_kurus"]),
                                "vadesi_gecmis_tl": tl(_vadesi_gecmis(s))} for s in en_buyuk],
        "not": "TL tutarlar fatura günündeki kurla; eşleşmemiş tahsilatlar alacaktan düşülmüştür.",
    }
    return d, sonuc, {"musteriler": [s["musteri_id"] for s in en_buyuk]}


def _geciken_alacaklar(b, a):
    d = _gun(b, a)
    g = metrikler.geciken_alacaklar(b.con, d, en_az_gun=a["min_gun"])
    gosterilen = g["faturalar"][:_en_fazla(a)]
    sonuc = {
        "min_gun": a["min_gun"], "fatura_sayisi": len(g["faturalar"]), "toplam_tl": tl(g["toplam_try_kurus"]),
        "faturalar": [{"fatura_id": f["fatura_id"], "musteri_id": f["musteri_id"], "musteri": f["ad"],
                       "vade_tarihi": f["vade_tarihi"].isoformat(), "gecikme_gun": f["gecikme_gun"],
                       "acik_tutar": tl(f["acik_kurus"]), "para_birimi": f["para_birimi"],
                       "acik_tutar_tl": tl(f["acik_try_kurus"])} for f in gosterilen],
    }
    return d, sonuc, {"faturalar": [f["fatura_id"] for f in gosterilen]}


def _musteri_ozeti(b, a):
    d = _gun(b, a)
    eslesen = musteri_bul(b, a["musteri_adi"], d)
    aciklama = _musteri_sonucu(eslesen)
    if aciklama:
        return d, aciklama, {}
    m = eslesen[0]
    mid = m["musteri_id"]
    yas = next((s for s in metrikler.yaslandirma(b.con, d)["musteriler"] if s["musteri_id"] == mid), None)
    acik = [f for f in metrikler.geciken_alacaklar(b.con, d, en_az_gun=-10**6)["faturalar"] if f["musteri_id"] == mid]
    tahsilat = satirlar(b.con, """
        SELECT tahsilat_id, tarih, tutar_kurus, para_birimi, yontem, fatura_id FROM tahsilatlar
        WHERE musteri_id = $m AND tarih <= $d ORDER BY tarih DESC, tahsilat_id DESC LIMIT 5""", {"m": mid, "d": d})
    cek = satirlar(b.con, """
        WITH son AS (
            SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS s
            FROM cek_hareketleri WHERE tarih <= $d)
        SELECT count(*) AS adet, coalesce(sum(c.tutar_kurus), 0) AS toplam
        FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
        WHERE c.musteri_id = $m AND c.alis_tarihi <= $d AND son.durum IN ('portfoyde', 'tahsilde')""",
                    {"m": mid, "d": d})[0]
    gecmis = metrikler.odeme_gecmisi(b.con, d)["musteriler"].get(mid)
    sonuc = {
        "musteri_id": mid, "musteri": m["ad"], "ulke": m["ulke"], "para_birimi": m["para_birimi"],
        "vade_gun": m["vade_gun"],
        "acik_alacak_tl": tl(yas["toplam_kurus"] if yas else 0), "dilimler": _dilimler(yas),
        "acik_fatura_sayisi": len(acik),
        "en_eski_acik_vade": min((f["vade_tarihi"] for f in acik), default=None),
        "son_tahsilatlar": [{"tahsilat_id": t["tahsilat_id"], "tarih": t["tarih"].isoformat(),
                             "tutar": tl(t["tutar_kurus"]), "para_birimi": t["para_birimi"], "yontem": t["yontem"],
                             "fatura_id": t["fatura_id"]} for t in tahsilat],
        "bekleyen_cek_sayisi": int(cek["adet"]), "bekleyen_cek_tutari": tl(cek["toplam"]),
        "son_12_ay_kapanan_fatura": gecmis["fatura_sayisi"] if gecmis else 0,
        "son_12_ay_gec_kapanan_fatura": gecmis["gec_kapanan"] if gecmis else 0,
        "son_12_ay_ortalama_gecikme_gun": gecmis["ortalama_gecikme_gun"] if gecmis else None,
    }
    if sonuc["en_eski_acik_vade"]:
        sonuc["en_eski_acik_vade"] = sonuc["en_eski_acik_vade"].isoformat()
    return d, sonuc, {"musteriler": [mid], "tahsilatlar": [t["tahsilat_id"] for t in tahsilat]}


def _dso_hesapla(b, a):
    d = _gun(b, a)
    gun = DONEMLER[a["donem"]]
    m = metrikler.dso(b.con, d, gun)
    sonuc = {
        "donem": a["donem"], "gun": gun, "pencere_baslangic": (d - dt.timedelta(days=gun - 1)).isoformat(),
        "pencere_bitis": d.isoformat(), "dso_gun": None if m["dso_gun"] is None else str(m["dso_gun"]),
        "acik_alacak_tl": tl(m["alacak_kurus"]), "donem_satisi_tl": tl(m["satis_kurus"]),
        "formul": "DSO = gün sayısı x açık alacak / dönemdeki net satış (iadeler düşülmüş). Kapsam tüm şirketse "
                  "POS satışları da dahildir.",
    }
    if m["dso_gun"] is None:
        sonuc["mesaj"] = "Bu dönemde satış olmadığı için DSO hesaplanamadı."
    return d, sonuc, {}


def _yaklasan_odemeler(b, a):
    d = b.simdi
    y = metrikler.yaklasan_odemeler(b.con, d, a["gun_sayisi"])
    gosterilen = y["odemeler"][:_en_fazla(a)]
    sonuc = {
        "gun_sayisi": a["gun_sayisi"], "fatura_sayisi": len(y["odemeler"]), "toplam_tl": tl(y["toplam_kurus"]),
        "vadesi_gecmis_tl": tl(sum(o["acik_kurus"] for o in y["odemeler"] if o["kalan_gun"] < 0)),
        "odemeler": [{"alis_fatura_id": o["alis_fatura_id"], "tedarikci": o["ad"], "kategori": o["kategori"],
                      "vade_tarihi": o["vade_tarihi"].isoformat(), "kalan_gun": o["kalan_gun"],
                      "tutar_tl": tl(o["acik_kurus"])} for o in gosterilen],
        "not": "Ödenmemiş tedarikçi (alış) faturalarıdır; maaş, SGK ve vergi ödemeleri dahil değildir.",
    }
    return d, sonuc, {"alis_faturalari": [o["alis_fatura_id"] for o in gosterilen]}


def _nakit_akis_tahmini(b, a):
    d = b.simdi
    n = metrikler.nakit_akis_tahmini(b.con, d, a["hafta_sayisi"])
    haftalar = [{"hafta": h["hafta"], "bas": h["bas"].isoformat(), "bit": h["bit"].isoformat(),
                 "giris_tl": tl(h["alacak_girisi_kurus"] + h["cek_girisi_kurus"] + h["pos_girisi_kurus"]),
                 "cikis_tl": tl(h["tedarikci_cikisi_kurus"]), "net_tl": tl(h["net_kurus"]),
                 "bilinen_kalemlerle_bakiye_tl": tl(h["bilinen_kalemlerle_bakiye_kurus"])} for h in n["haftalar"]]
    sonuc = {
        "acilis_tl": tl(n["acilis_kurus"]), "haftalar": haftalar,
        "supheli_alacak_tl": tl(n["supheli_alacak_kurus"]), "ufuk_disi_giris_tl": tl(n["ufuk_disi_giris_kurus"]),
        "ufuk_disi_cikis_tl": tl(n["ufuk_disi_cikis_kurus"]), "dahil_olmayanlar": n["dahil_olmayanlar"],
        "not": "Bu bir bakiye tahmini değildir: yalnızca bilinen kalemler (açık alacaklar, çekler, POS valörleri, "
               "ödenmemiş alış faturaları) vardır. Vadesi geçmiş alacaklar 1. haftaya yazılır; vadesini 90 günden "
               "fazla geçmiş alacaklar şüpheli sayılıp dışarıda bırakılır.",
    }
    return d, sonuc, {}


def _kur_riski(b, a):
    d = _gun(b, a)
    k = metrikler.kur_riski(b.con, d)
    dovizler = [x for x in k["dovizler"] if a.get("para_birimi") in (None, x["para_birimi"])]
    sonuc = {
        "dovizler": [{"para_birimi": x["para_birimi"], "alacak": tl(x["alacak_doviz_kurus"]),
                      "banka": tl(x["banka_doviz_kurus"]), "toplam": tl(x["toplam_doviz_kurus"]),
                      "kur": kur_metni(x["kur"]), "toplam_tl": tl(x["toplam_try_kurus"]),
                      "kur_yuzde_10_degisirse_etki_tl": tl(x["yuzde_10_etki_kurus"]),
                      "alacak_kur_farki_tl": tl(x["alacak_kur_farki_kurus"])} for x in dovizler],
        "not": "Alacak ve banka tutarları döviz cinsindendir; TL karşılığı o günün TCMB alış kuruyla.",
    }
    return d, sonuc, {}


def _doviz_kuru(b, a):
    d = _gun(b, a)
    s = satirlar(b.con, "SELECT kur_id, tarih, alis FROM doviz_kurlari WHERE para_birimi = $pb AND tarih <= $d "
                        "ORDER BY tarih DESC LIMIT 1", {"pb": a["para_birimi"], "d": d})
    if not s:
        return d, {"bulunamadi": True, "mesaj": "Bu gün için kur bulunamadı."}, {}
    return d, {"para_birimi": a["para_birimi"], "kur_tarihi": s[0]["tarih"].isoformat(), "alis": kur_metni(s[0]["alis"]),
               "not": "TCMB döviz alış kuru; hafta sonu ve tatilde son iş gününün kuru."}, \
        {"doviz_kurlari": [s[0]["kur_id"]]}


def _cek_portfoyu(b, a):
    d = _gun(b, a)
    durumlar = CEK_DURUMLARI[a.get("durum", "bekleyen")]
    p = {"d": d, "n": _en_fazla(a)}
    vade = ""
    if a.get("vade_gun") is not None:
        vade = " AND c.vade_tarihi BETWEEN $d AND CAST($d AS DATE) + CAST($vg AS INTEGER)"
        p["vg"] = a["vade_gun"]
    durum_listesi = ", ".join(f"'{x}'" for x in durumlar)  # sabit küme, kullanıcı girdisi değil
    taban = f"""
        WITH son AS (
            SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS s
            FROM cek_hareketleri WHERE tarih <= $d),
        cek AS (
            SELECT c.cek_id, c.musteri_id, m.ad, c.kesideci_banka, c.vade_tarihi, c.tutar_kurus, son.durum
            FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
            LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
            WHERE c.alis_tarihi <= $d AND son.durum IN ({durum_listesi}){vade})"""
    ozet = satirlar(b.con, taban + " SELECT durum, count(*) AS adet, sum(tutar_kurus) AS toplam FROM cek "
                                   "GROUP BY durum ORDER BY durum", {k: v for k, v in p.items() if k != "n"})
    liste = satirlar(b.con, taban + " SELECT * FROM cek ORDER BY vade_tarihi, cek_id LIMIT $n", p)
    sonuc = {
        "durum": a.get("durum", "bekleyen"), "cek_sayisi": sum(int(o["adet"]) for o in ozet),
        "toplam_tl": tl(sum(int(o["toplam"]) for o in ozet)),
        "durumlara_gore": [{"durum": o["durum"], "adet": int(o["adet"]), "toplam_tl": tl(o["toplam"])} for o in ozet],
        "cekler": [{"cek_id": c["cek_id"], "musteri": c["ad"], "kesideci_banka": c["kesideci_banka"],
                    "vade_tarihi": c["vade_tarihi"].isoformat(), "tutar_tl": tl(c["tutar_kurus"]), "durum": c["durum"]}
                   for c in liste],
    }
    return d, sonuc, {"cekler": [c["cek_id"] for c in liste]}


def _pos_tahsilatlari(b, a):
    bas, bit = _gun(b, a, "baslangic"), _gun(b, a, "bitis")
    if bas > bit:
        raise AracHatasi("Başlangıç günü bitiş gününden sonra olamaz.")
    s = satirlar(b.con, """
        SELECT count(*) AS adet, coalesce(sum(CASE WHEN tutar_kurus > 0 THEN tutar_kurus END), 0) AS satis,
               coalesce(sum(CASE WHEN tutar_kurus < 0 THEN tutar_kurus END), 0) AS iade,
               coalesce(sum(CASE WHEN tutar_kurus < 0 THEN 1 ELSE 0 END), 0) AS iade_adet,
               coalesce(sum(komisyon_kurus), 0) AS komisyon,
               coalesce(sum(tutar_kurus - komisyon_kurus), 0) AS net,
               coalesce(sum(CASE WHEN valor_tarihi > $simdi THEN tutar_kurus - komisyon_kurus END), 0) AS bekleyen
        FROM pos_islemleri WHERE islem_tarihi BETWEEN $bas AND $bit""", {"bas": bas, "bit": bit, "simdi": b.simdi})[0]
    sonuc = {"baslangic": bas.isoformat(), "bitis": bit.isoformat(), "islem_sayisi": int(s["adet"]),
             "iade_sayisi": int(s["iade_adet"]), "satis_tl": tl(s["satis"]), "iade_tl": tl(s["iade"]),
             "komisyon_tl": tl(s["komisyon"]), "net_tl": tl(s["net"]),
             "valoru_gelmemis_net_tl": tl(s["bekleyen"])}
    return bit, sonuc, {}


def _fatura_ara(b, a):
    d = _gun(b, a)
    kosul, p = ["f.fatura_tarihi <= $d"], {"d": d, "n": _en_fazla(a)}
    if a.get("fatura_no"):
        kosul.append("(lower(f.fatura_id) = lower($no) OR lower(f.kaynak_fatura_no) = lower($no))")
        p["no"] = a["fatura_no"].strip()
    if a.get("musteri_adi"):
        eslesen = musteri_bul(b, a["musteri_adi"], d)
        aciklama = _musteri_sonucu(eslesen)
        if aciklama:
            return d, aciklama, {}
        kosul.append("f.musteri_id = $m")
        p["m"] = eslesen[0]["musteri_id"]
    durum = a.get("durum", "hepsi")
    if durum == "acik":
        kosul.append("f.tur = 'satis' AND a.acik_kurus > 0")
    elif durum == "kapali":
        kosul.append("f.tur = 'satis' AND a.acik_kurus <= 0")
    elif durum == "iade":
        kosul.append("f.tur = 'iade'")
    taban = f"""
        WITH {metrikler.ACIK}
        SELECT f.fatura_id, f.kaynak_fatura_no, f.tur, f.musteri_id, m.ad, f.fatura_tarihi, f.vade_tarihi,
               f.para_birimi, f.tutar_kurus, f.tutar_try_kurus, a.acik_kurus, f.iliskili_fatura_id
        FROM faturalar f LEFT JOIN acik a ON a.fatura_id = f.fatura_id
        LEFT JOIN musteriler m ON m.musteri_id = f.musteri_id
        WHERE {' AND '.join(kosul)}"""
    adet = satirlar(b.con, f"SELECT count(*) AS n FROM ({taban})", {k: v for k, v in p.items() if k != "n"})[0]["n"]
    liste = satirlar(b.con, taban + " ORDER BY f.fatura_tarihi DESC, f.fatura_id DESC LIMIT $n", p)
    faturalar = []
    for f in liste:
        if f["tur"] == "iade":
            durum_f = "iade"
        else:
            durum_f = "acik" if f["acik_kurus"] > 0 else "kapali"
        satir = {"fatura_id": f["fatura_id"], "kaynak_fatura_no": f["kaynak_fatura_no"], "tur": f["tur"],
                 "musteri": f["ad"], "fatura_tarihi": f["fatura_tarihi"].isoformat(),
                 "vade_tarihi": f["vade_tarihi"].isoformat(), "tutar": tl(f["tutar_kurus"]),
                 "para_birimi": f["para_birimi"], "tutar_tl": tl(f["tutar_try_kurus"]), "durum": durum_f}
        if f["tur"] == "satis":
            satir["acik_tutar"] = tl(max(int(f["acik_kurus"]), 0))
        else:
            satir["iliskili_fatura_id"] = f["iliskili_fatura_id"]
        faturalar.append(satir)
    return d, {"bulunan": int(adet), "gosterilen": len(faturalar), "faturalar": faturalar}, \
        {"faturalar": [f["fatura_id"] for f in faturalar]}


def _kisalt(metin: str, sinir: int) -> tuple[str, bool]:
    if len(metin) <= sinir:
        return metin, False
    kes = metin.rfind(" ", 0, sinir)
    return metin[:kes if kes > 0 else sinir].rstrip() + " …", True


def _belge_ara(b, a):
    if b.belge is None or b.rol is None:
        raise AracKullanilamaz("Belge araması şu an kullanılamıyor.")
    bulunan = b.belge.ara(a["sorgu"], b.rol, a.get("en_fazla", BELGE_VARSAYILAN), b.belge_yontemi)
    sonuclar = []
    for p, _ in bulunan:
        metin, kesildi = _kisalt(p.metin, BELGE_METIN_SINIRI)
        s = {"parca_id": p.parca_id, "belge": p.belge_baslik, "madde": p.madde_baslik, "metin": metin}
        if kesildi:
            s["metin_kesildi"] = True
        sonuclar.append(s)
    return b.simdi, {"sorgu": a["sorgu"], "sonuclar": sonuclar}, \
        {"belge_parcalari": [s["parca_id"] for s in sonuclar]}


# ---------------------------------------------------------------- tanımlar

ARACLAR: list[Arac] = [
    Arac("nakit_pozisyonu", "Bankalardaki nakit: her hesabın bakiyesi ve toplamın TL karşılığı.",
         {"tarih": TARIH}, _nakit_pozisyonu),
    Arac("banka_hareketleri", "Bir tarih aralığındaki banka hareketlerini listeler; para birimine göre giriş ve "
                              "çıkış toplamlarını verir.",
         {"baslangic": {**TARIH, "description": "Aralığın ilk günü, YYYY-MM-DD"},
          "bitis": {**TARIH, "description": "Aralığın son günü, YYYY-MM-DD"},
          "hesap_id": {**KIMLIK, "description": "Tek bir hesap için hesap kimliği"},
          "yon": {"type": "string", "enum": ["giris", "cikis", "hepsi"], "description": "Para girişi mi çıkışı mı"},
          "en_fazla": EN_FAZLA},
         _banka_hareketleri, ("baslangic", "bitis")),
    Arac("alacak_yaslandirma", "Açık alacakları vadeden bu yana geçen güne göre dilimlere ayırır (vadesi gelmemiş, "
                               "1-30, 31-60, 61-90, 91-180, 180 gün üstü); en büyük alacaklı müşterileri listeler.",
         {"tarih": TARIH, "musteri_adi": {**AD, "description": "Yalnızca bir müşteri için müşteri adı"},
          "en_fazla": EN_FAZLA},
         _alacak_yaslandirma),
    Arac("geciken_alacaklar", "Vadesini en az min_gun gün geçmiş açık faturaları, tutarı büyükten küçüğe listeler.",
         {"min_gun": {"type": "integer", "minimum": 1, "maximum": 3650,
                      "description": "En az kaç gün gecikmiş olanlar"},
          "en_fazla": EN_FAZLA, "tarih": TARIH},
         _geciken_alacaklar, ("min_gun",)),
    Arac("musteri_ozeti", "Bir müşterinin açık alacağını, son tahsilatlarını, bekleyen çeklerini ve son 12 aydaki "
                          "ödeme alışkanlığını özetler.",
         {"musteri_adi": {**AD, "description": "Müşteri adı (en az 3 harf)"}, "tarih": TARIH},
         _musteri_ozeti, ("musteri_adi",)),
    Arac("dso_hesapla", "Alacakların ortalama kaç günde tahsil edildiğini (DSO) seçilen dönem için hesaplar.",
         {"donem": {"type": "string", "enum": list(DONEMLER), "description": "Hesaplama dönemi"}, "tarih": TARIH},
         _dso_hesapla, ("donem",)),
    Arac("yaklasan_odemeler", "Önümüzdeki günlerde vadesi gelen ve vadesi geçmiş ödenmemiş tedarikçi faturalarını "
                              "listeler.",
         {"gun_sayisi": {"type": "integer", "minimum": 1, "maximum": 90,
                         "description": "Bugünden itibaren kaç günlük ödemeler"}, "en_fazla": EN_FAZLA},
         _yaklasan_odemeler, ("gun_sayisi",)),
    Arac("nakit_akis_tahmini", "Önümüzdeki haftaların bilinen nakit giriş ve çıkışlarını haftalık verir.",
         {"hafta_sayisi": {"type": "integer", "minimum": 1, "maximum": 13, "description": "Kaç haftalık (1-13)"}},
         _nakit_akis_tahmini, ("hafta_sayisi",)),
    Arac("kur_riski", "Döviz cinsinden alacak ve banka bakiyelerinin açık pozisyonunu ve kur %10 değişirse TL "
                      "etkisini gösterir.",
         {"para_birimi": {"type": "string", "enum": ["EUR", "USD"], "description": "Yalnızca bir para birimi"},
          "tarih": TARIH},
         _kur_riski),
    Arac("doviz_kuru", "TCMB döviz alış kurunu verir.",
         {"para_birimi": {"type": "string", "enum": ["USD", "EUR", "GBP"], "description": "Para birimi"},
          "tarih": TARIH},
         _doviz_kuru, ("para_birimi",)),
    Arac("cek_portfoyu", "Müşterilerden alınan çekleri durumuna ve vadesine göre listeler.",
         {"durum": {"type": "string", "enum": list(CEK_DURUMLARI),
                    "description": "Çekin durumu; bekleyen = portföyde ya da tahsilde (varsayılan)"},
          "vade_gun": {"type": "integer", "minimum": 0, "maximum": 365,
                       "description": "Vadesine en fazla kaç gün kalanlar"},
          "en_fazla": EN_FAZLA, "tarih": TARIH},
         _cek_portfoyu),
    Arac("pos_tahsilatlari", "Bir tarih aralığındaki POS satışlarını, iadeleri ve komisyonu özetler.",
         {"baslangic": {**TARIH, "description": "Aralığın ilk günü, YYYY-MM-DD"},
          "bitis": {**TARIH, "description": "Aralığın son günü, YYYY-MM-DD"}},
         _pos_tahsilatlari, ("baslangic", "bitis")),
    Arac("fatura_ara", "Satış ve iade faturalarını fatura numarasına, müşteriye ya da duruma göre arar.",
         {"fatura_no": {**KIMLIK, "description": "Fatura numarası"},
          "musteri_adi": {**AD, "description": "Müşteri adı"},
          "durum": {"type": "string", "enum": ["acik", "kapali", "iade", "hepsi"], "description": "Faturanın durumu"},
          "en_fazla": EN_FAZLA, "tarih": TARIH},
         _fatura_ara),
    Arac("belge_ara", "Sözleşmelerde, şirket politikalarında ve ilgili kanun maddelerinde arar; en ilgili maddelerin "
                      "metnini parça kimliğiyle döndürür. Rolün göremediği belgeler aranmaz.",
         {"sorgu": {"type": "string", "minLength": 3, "maxLength": 300,
                    "description": "Ne aranıyor; ör. 'Bereket İmalat ödeme vadesi'"},
          "en_fazla": {"type": "integer", "minimum": 1, "maximum": BELGE_EN_FAZLA,
                       "description": f"En fazla kaç madde (varsayılan {BELGE_VARSAYILAN})"}},
         _belge_ara, ("sorgu",)),
]

ARACLAR_ADA_GORE: dict[str, Arac] = {a.ad: a for a in ARACLAR}
TOPLU_ARACLAR = frozenset({"dso_hesapla", "nakit_akis_tahmini", "kur_riski", "pos_tahsilatlari"})
