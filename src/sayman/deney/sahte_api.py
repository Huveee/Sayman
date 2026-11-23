"""Faz 9: sahte ERP ve banka API'si (FastAPI). Otomatik araçlar bunun OpenAPI tanımından üretilir.

Bu API, bir şirketin elinde hazır bulunan sıradan bir ERP ve açık bankacılık API'sini taklit eder. Tasarım kararları
soru setine bakılarak değil, "tipik bir ERP / banka API'si ne sunar" sorusuna göre verildi ve ölçümden önce yazıldı:

- Yalnızca kayıt uç noktaları: liste (süzgeç + sayfalama) ve tek kayıt. Hesaplanmış rapor uç noktası yok (yaşlandırma,
  DSO, geciken alacaklar, nakit akış tahmini, kur riski): ERP'ler bunları ekranda rapor olarak verir, API'de
  çoğunlukla vermez.
- İki hazır toplam var, çünkü ERP'lerde ve açık bankacılıkta (ÖHVPS) olağan alanlardır: müşterinin cari bakiyesi
  (açık faturaların TL karşılığı toplamı) ve banka hesabının bakiyesi.
- Listeler sayfalıdır: sayfa_boyutu varsayılan 25, en çok 100; her cevap toplam_kayit sayısını da verir. Sıralama
  her listede sabittir (tarihe ya da vadeye göre); tutara göre sıralama yoktur.
- Tutarlar kendi para biriminde ondalık metindir (ör. "1234.56"), kurlar da öyle: açık bankacılık API'lerinde
  olağandır (para kayan noktalı sayıyla taşınmaz). Faturalarda TL karşılığı da vardır. Açıklama: ilk taslakta
  tutarlar sayıydı (1234.56); doğrulayıcının kaynak taraması (dogrulayici._degerlerden) kayan noktalı sayıları
  kaynak saymadığı için otomatik koldaki her tutar "kaynaksız" olurdu. Doğrulayıcı iki kolda aynı kalsın diye
  API metne çevrildi (ölçümden önce).
- Alan adları şirketin veri modelini izler (musteri_id, musteri, fatura_id ...).
- Yalnızca GET. Hiçbir uç nokta yazmaz, silmez, para hareket ettirmez.
- Bugün: sunucunun saati (simülasyon günü). Bugünden sonraki kayıtlar hiçbir uçta görünmez.

Yetki: istek API kapısından (sayman.deney.otomatik.OtomatikSunucu) gelir; kapı kimlik kartını doğrular ve rolü
X-Rol başlığıyla iletir. API rolü kapsamlı veritabanı bağlantısına çevirir (Faz 3 ile aynı kapsam kopyaları) ve satış
rollerine banka, tedarikçi, ödeme ve POS uçlarını kapatır (403). Böylece iki kolda da yetki veri katmanındadır.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.security import APIKeyHeader

from sayman.ambar import metrikler
from sayman.ambar.kurulum import satirlar
from sayman.araclar.araclar import kur_metni, tl

SAYFA_VARSAYILAN, SAYFA_EN_COK = 25, 100
SATIS_KAPALI = "Bu rol banka, tedarikçi, ödeme ve POS verisini göremez."


def _tl(kurus) -> str | None:
    return None if kurus is None else tl(kurus)


def _gun(x) -> str | None:
    return x.isoformat() if isinstance(x, (dt.date, dt.datetime)) else x


class Ortam:
    """Rol → (kapsam, bağlantı) ve sunucunun bugünü."""

    def __init__(self, baglantilar: dict, roller: dict[str, str], bugun: Callable[[], dt.date]):
        self.baglantilar, self.roller, self.bugun = baglantilar, roller, bugun


def uygulama(ortam: Ortam) -> FastAPI:
    app = FastAPI(title="Sahte ERP ve Banka API'si", version="1.0.0",
                  description="Müşteri, fatura, tahsilat, çek, tedarikçi, POS ve banka kayıtları (salt okunur).")

    class Istek:
        def __init__(self, rol: str):
            if rol not in ortam.roller:
                raise HTTPException(401, "Rol tanınmadı.")
            self.rol, self.kapsam = rol, ortam.roller[rol]
            self.con, self.d = ortam.baglantilar[self.kapsam], ortam.bugun()

        def yalniz_cfo(self):
            if self.kapsam != "hepsi":
                raise HTTPException(403, SATIS_KAPALI)

    rol_basligi = APIKeyHeader(name="X-Rol", description="API kapısının doğrulanmış kimlikten koyduğu rol")

    def istek(x_rol: str = Depends(rol_basligi)) -> Istek:
        return Istek(x_rol)

    def sayfala(r: Istek, sql: str, p: dict, sayfa: int, boyut: int, cevir) -> dict:
        toplam = satirlar(r.con, f"SELECT count(*) AS n FROM ({sql})", p)[0]["n"]
        liste = satirlar(r.con, sql + " LIMIT $lim OFFSET $ofs", {**p, "lim": boyut, "ofs": (sayfa - 1) * boyut})
        return {"toplam_kayit": int(toplam), "sayfa": sayfa, "sayfa_boyutu": boyut, "kayitlar": [cevir(x) for x in liste]}

    SAYFA = Query(1, ge=1, description="Sayfa numarası (1'den başlar)")
    BOYUT = Query(SAYFA_VARSAYILAN, ge=1, le=SAYFA_EN_COK, description="Sayfadaki kayıt sayısı")

    # ------------------------------------------------------------------ müşteriler

    def _musteri(x):
        return {"musteri_id": x["musteri_id"], "ad": x["ad"], "ulke": x["ulke"], "para_birimi": x["para_birimi"],
                "vade_gun": x["vade_gun"], "ilk_islem_tarihi": _gun(x["ilk_islem_tarihi"])}

    @app.get("/api/v1/musteriler", tags=["ERP"], summary="Müşterileri listeler")
    def musteriler(ad: str | None = Query(None, description="Müşteri adında geçen metin"),
                   ulke: str | None = Query(None, description="Ülke adı (ör. Türkiye, Almanya)"),
                   sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        kosul, p = ["ilk_islem_tarihi <= $d"], {"d": r.d}
        if ad:
            kosul.append("lower(ad) LIKE '%' || lower($ad) || '%'")
            p["ad"] = ad
        if ulke:
            kosul.append("ulke = $ulke")
            p["ulke"] = ulke
        sql = f"SELECT * FROM musteriler WHERE {' AND '.join(kosul)} ORDER BY ad, musteri_id"
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, _musteri)

    @app.get("/api/v1/musteriler/{musteri_id}", tags=["ERP"], summary="Müşteri kartı ve cari bakiyesi")
    def musteri(musteri_id: str = Path(..., description="Müşteri kimliği (ör. M00001)"), r: Istek = Depends(istek)):
        x = satirlar(r.con, "SELECT * FROM musteriler WHERE musteri_id = $m AND ilk_islem_tarihi <= $d",
                     {"m": musteri_id, "d": r.d})
        if not x:
            raise HTTPException(404, "Müşteri bulunamadı.")
        b = satirlar(r.con, f"WITH {metrikler.ACIK} SELECT coalesce(sum(acik_try_kurus), 0) AS b, count(*) AS n "
                            "FROM acik_pozitif WHERE musteri_id = $m", {"m": musteri_id, "d": r.d})[0]
        return {**_musteri(x[0]), "cari_bakiye_tl": _tl(b["b"]), "acik_fatura_sayisi": int(b["n"])}

    # ------------------------------------------------------------------ faturalar

    def _fatura(x):
        f = {"fatura_id": x["fatura_id"], "kaynak_fatura_no": x["kaynak_fatura_no"], "tur": x["tur"],
             "musteri_id": x["musteri_id"], "musteri": x["ad"], "fatura_tarihi": _gun(x["fatura_tarihi"]),
             "vade_tarihi": _gun(x["vade_tarihi"]), "para_birimi": x["para_birimi"], "tutar": _tl(x["tutar_kurus"]),
             "tutar_tl": _tl(x["tutar_try_kurus"])}
        if x["tur"] == "satis":
            kalan = max(int(x["acik_kurus"]), 0)
            f.update(kalan_tutar=_tl(kalan), durum="acik" if kalan > 0 else "kapali")
        else:
            f.update(iliskili_fatura_id=x["iliskili_fatura_id"], durum="iade")
        return f

    FATURA_TABAN = f"""
        WITH {metrikler.ACIK}
        SELECT f.*, m.ad, a.acik_kurus FROM faturalar f
        LEFT JOIN acik a ON a.fatura_id = f.fatura_id LEFT JOIN musteriler m ON m.musteri_id = f.musteri_id
        WHERE f.fatura_tarihi <= $d"""

    @app.get("/api/v1/faturalar", tags=["ERP"], summary="Satış ve iade faturalarını listeler")
    def faturalar(musteri_id: str | None = Query(None, description="Müşteri kimliği"),
                  musteri_adi: str | None = Query(None, description="Müşteri adında geçen metin"),
                  fatura_no: str | None = Query(None, description="Fatura numarası ya da kaynak sistemdeki numarası"),
                  tur: Literal["satis", "iade"] | None = Query(None, description="Fatura türü"),
                  durum: Literal["acik", "kapali"] | None = Query(None, description="Satış faturasının durumu"),
                  baslangic: dt.date | None = Query(None, description="Fatura tarihi en erken (YYYY-MM-DD)"),
                  bitis: dt.date | None = Query(None, description="Fatura tarihi en geç (YYYY-MM-DD)"),
                  vade_baslangic: dt.date | None = Query(None, description="Vade tarihi en erken"),
                  vade_bitis: dt.date | None = Query(None, description="Vade tarihi en geç"),
                  siralama: Literal["tarih_azalan", "tarih_artan", "vade_artan"] = Query(
                      "tarih_azalan", description="Sıralama"),
                  sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        kosul, p = [], {"d": r.d}
        for alan, deger, ifade in (("m", musteri_id, "f.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("no", fatura_no, "(lower(f.fatura_id) = lower($no) OR lower(f.kaynak_fatura_no) = lower($no))"),
                                   ("tur", tur, "f.tur = $tur"), ("bas", baslangic, "f.fatura_tarihi >= $bas"),
                                   ("bit", bitis, "f.fatura_tarihi <= $bit"), ("vb", vade_baslangic, "f.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "f.vade_tarihi <= $vs")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger.strip() if isinstance(deger, str) else deger
        if durum == "acik":
            kosul.append("f.tur = 'satis' AND a.acik_kurus > 0")
        elif durum == "kapali":
            kosul.append("f.tur = 'satis' AND a.acik_kurus <= 0")
        sira = {"tarih_azalan": "f.fatura_tarihi DESC, f.fatura_id DESC", "tarih_artan": "f.fatura_tarihi, f.fatura_id",
                "vade_artan": "f.vade_tarihi, f.fatura_id"}[siralama]
        sql = FATURA_TABAN + "".join(f" AND {k}" for k in kosul) + f" ORDER BY {sira}"
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, _fatura)

    @app.get("/api/v1/faturalar/{fatura_id}", tags=["ERP"], summary="Tek bir faturayı getirir")
    def fatura(fatura_id: str = Path(..., description="Fatura numarası (ör. F000001)"), r: Istek = Depends(istek)):
        x = satirlar(r.con, FATURA_TABAN + " AND f.fatura_id = $f", {"d": r.d, "f": fatura_id})
        if not x:
            raise HTTPException(404, "Fatura bulunamadı.")
        return _fatura(x[0])

    # ------------------------------------------------------------------ tahsilatlar ve çekler

    @app.get("/api/v1/tahsilatlar", tags=["ERP"], summary="Müşterilerden yapılan tahsilatları listeler")
    def tahsilatlar(musteri_id: str | None = Query(None, description="Müşteri kimliği"),
                    musteri_adi: str | None = Query(None, description="Müşteri adında geçen metin"),
                    baslangic: dt.date | None = Query(None, description="Tahsilat tarihi en erken"),
                    bitis: dt.date | None = Query(None, description="Tahsilat tarihi en geç"),
                    yontem: Literal["havale", "swift", "cek"] | None = Query(None, description="Ödeme yöntemi"),
                    sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        kosul, p = ["t.tarih <= $d"], {"d": r.d}
        for alan, deger, ifade in (("m", musteri_id, "t.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("bas", baslangic, "t.tarih >= $bas"), ("bit", bitis, "t.tarih <= $bit"),
                                   ("y", yontem, "t.yontem = $y")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        sql = (f"SELECT t.*, m.ad FROM tahsilatlar t LEFT JOIN musteriler m ON m.musteri_id = t.musteri_id "
               f"WHERE {' AND '.join(kosul)} ORDER BY t.tarih DESC, t.tahsilat_id DESC")
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "tahsilat_id": x["tahsilat_id"], "musteri_id": x["musteri_id"], "musteri": x["ad"],
            "fatura_id": x["fatura_id"], "tarih": _gun(x["tarih"]), "para_birimi": x["para_birimi"],
            "tutar": _tl(x["tutar_kurus"]), "yontem": x["yontem"], "cek_id": x["cek_id"]})

    @app.get("/api/v1/cekler", tags=["ERP"], summary="Müşterilerden alınan çekleri listeler")
    def cekler(musteri_id: str | None = Query(None, description="Müşteri kimliği"),
               musteri_adi: str | None = Query(None, description="Müşteri adında geçen metin"),
               durum: Literal["portfoyde", "tahsilde", "tahsil_edildi", "karsiliksiz"] | None = Query(
                   None, description="Çekin bugünkü durumu"),
               vade_baslangic: dt.date | None = Query(None, description="Vade tarihi en erken"),
               vade_bitis: dt.date | None = Query(None, description="Vade tarihi en geç"),
               sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        kosul, p = [], {"d": r.d}
        for alan, deger, ifade in (("m", musteri_id, "c.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("du", durum, "son.durum = $du"), ("vb", vade_baslangic, "c.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "c.vade_tarihi <= $vs")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        sql = f"""
            WITH son AS (SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC,
                                cek_hareket_id DESC) AS s FROM cek_hareketleri WHERE tarih <= $d)
            SELECT c.*, m.ad, son.durum FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
            LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
            WHERE c.alis_tarihi <= $d{''.join(f' AND {k}' for k in kosul)} ORDER BY c.vade_tarihi, c.cek_id"""
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "cek_id": x["cek_id"], "cek_no": x["cek_no"], "musteri_id": x["musteri_id"], "musteri": x["ad"],
            "kesideci_banka": x["kesideci_banka"], "alis_tarihi": _gun(x["alis_tarihi"]),
            "vade_tarihi": _gun(x["vade_tarihi"]), "tutar": _tl(x["tutar_kurus"]), "durum": x["durum"]})

    # ------------------------------------------------------------------ tedarikçiler ve ödemeler (yalnız CFO)

    @app.get("/api/v1/tedarikciler", tags=["ERP"], summary="Tedarikçileri listeler")
    def tedarikciler(ad: str | None = Query(None, description="Tedarikçi adında geçen metin"),
                     sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        r.yalniz_cfo()
        kosul, p = "", {}
        if ad:
            kosul, p = " WHERE lower(ad) LIKE '%' || lower($ad) || '%'", {"ad": ad}
        return sayfala(r, f"SELECT * FROM tedarikciler{kosul} ORDER BY ad, tedarikci_id", p, sayfa, sayfa_boyutu,
                       lambda x: {k: x[k] for k in ("tedarikci_id", "ad", "kategori", "vade_gun")})

    @app.get("/api/v1/alis-faturalari", tags=["ERP"], summary="Tedarikçi (alış) faturalarını listeler")
    def alis_faturalari(tedarikci_id: str | None = Query(None, description="Tedarikçi kimliği"),
                        durum: Literal["acik", "kapali"] | None = Query(None, description="Ödenme durumu"),
                        vade_baslangic: dt.date | None = Query(None, description="Vade tarihi en erken"),
                        vade_bitis: dt.date | None = Query(None, description="Vade tarihi en geç"),
                        sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        r.yalniz_cfo()
        kosul, p = [], {"d": r.d}
        for alan, deger, ifade in (("t", tedarikci_id, "b.tedarikci_id = $t"), ("vb", vade_baslangic, "b.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "b.vade_tarihi <= $vs")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        if durum:
            kosul.append("b.acik_kurus > 0" if durum == "acik" else "b.acik_kurus <= 0")
        sql = (f"WITH {metrikler.ACIK_BORC} SELECT b.*, t.ad, a.tutar_kurus FROM acik_borc b "
               f"JOIN alis_faturalari a ON a.alis_fatura_id = b.alis_fatura_id "
               f"LEFT JOIN tedarikciler t ON t.tedarikci_id = b.tedarikci_id"
               f"{' WHERE ' + ' AND '.join(kosul) if kosul else ''} ORDER BY b.vade_tarihi, b.alis_fatura_id")
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "alis_fatura_id": x["alis_fatura_id"], "tedarikci_id": x["tedarikci_id"], "tedarikci": x["ad"],
            "kategori": x["kategori"], "tarih": _gun(x["tarih"]), "vade_tarihi": _gun(x["vade_tarihi"]),
            "tutar": _tl(x["tutar_kurus"]), "kalan_tutar": _tl(max(int(x["acik_kurus"]), 0))})

    @app.get("/api/v1/odemeler", tags=["ERP"], summary="Yapılan ödemeleri listeler")
    def odemeler(tedarikci_id: str | None = Query(None, description="Tedarikçi kimliği"),
                 baslangic: dt.date | None = Query(None, description="Ödeme tarihi en erken"),
                 bitis: dt.date | None = Query(None, description="Ödeme tarihi en geç"),
                 sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        r.yalniz_cfo()
        kosul, p = ["tarih <= $d"], {"d": r.d}
        for alan, deger, ifade in (("t", tedarikci_id, "tedarikci_id = $t"), ("bas", baslangic, "tarih >= $bas"),
                                   ("bit", bitis, "tarih <= $bit")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        sql = f"SELECT * FROM odemeler WHERE {' AND '.join(kosul)} ORDER BY tarih DESC, odeme_id DESC"
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "odeme_id": x["odeme_id"], "tur": x["tur"], "tedarikci_id": x["tedarikci_id"],
            "alis_fatura_id": x["alis_fatura_id"], "tarih": _gun(x["tarih"]), "tutar": _tl(x["tutar_kurus"]),
            "yontem": x["yontem"], "aciklama": x["aciklama"]})

    @app.get("/api/v1/pos-islemleri", tags=["ERP"], summary="POS (kartlı satış) işlemlerini listeler")
    def pos_islemleri(baslangic: dt.date | None = Query(None, description="İşlem tarihi en erken"),
                      bitis: dt.date | None = Query(None, description="İşlem tarihi en geç"),
                      sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        r.yalniz_cfo()
        kosul, p = ["islem_tarihi <= $d"], {"d": r.d}
        for alan, deger, ifade in (("bas", baslangic, "islem_tarihi >= $bas"), ("bit", bitis, "islem_tarihi <= $bit")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        sql = f"SELECT * FROM pos_islemleri WHERE {' AND '.join(kosul)} ORDER BY islem_tarihi DESC, pos_id DESC"
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "pos_id": x["pos_id"], "islem_tarihi": _gun(x["islem_tarihi"]), "tutar": _tl(x["tutar_kurus"]),
            "komisyon": _tl(x["komisyon_kurus"]), "valor_tarihi": _gun(x["valor_tarihi"]),
            "kaynak_fatura_no": x["kaynak_fatura_no"]})

    # ------------------------------------------------------------------ kurlar (herkese açık)

    @app.get("/api/v1/kurlar", tags=["ERP"], summary="TCMB döviz alış kuru")
    def kurlar(para_birimi: Literal["USD", "EUR", "GBP"] = Query(..., description="Para birimi"),
               tarih: dt.date | None = Query(None, description="Gün (verilmezse bugün); o gün yoksa önceki iş günü"),
               r: Istek = Depends(istek)):
        g = min(tarih or r.d, r.d)
        x = satirlar(r.con, "SELECT tarih, alis FROM doviz_kurlari WHERE para_birimi = $pb AND tarih <= $g "
                            "ORDER BY tarih DESC LIMIT 1", {"pb": para_birimi, "g": g})
        if not x:
            raise HTTPException(404, "Kur bulunamadı.")
        return {"para_birimi": para_birimi, "tarih": _gun(x[0]["tarih"]), "alis": kur_metni(x[0]["alis"])}

    # ------------------------------------------------------------------ banka (açık bankacılık benzeri, yalnız CFO)

    @app.get("/api/v1/banka/hesaplar", tags=["Banka"], summary="Banka hesaplarını listeler")
    def hesaplar(r: Istek = Depends(istek)):
        r.yalniz_cfo()
        liste = satirlar(r.con, "SELECT * FROM banka_hesaplari WHERE acilis_tarihi <= $d ORDER BY hesap_id", {"d": r.d})
        return {"hesaplar": [{"hesap_id": x["hesap_id"], "banka": x["banka"], "para_birimi": x["para_birimi"],
                              "acilis_tarihi": _gun(x["acilis_tarihi"])} for x in liste]}

    @app.get("/api/v1/banka/hesaplar/{hesap_id}/bakiye", tags=["Banka"], summary="Hesap bakiyesi")
    def bakiye(hesap_id: str = Path(..., description="Hesap kimliği (ör. A_TRY)"),
               tarih: dt.date | None = Query(None, description="Gün sonu (verilmezse bugün)"),
               r: Istek = Depends(istek)):
        r.yalniz_cfo()
        g = min(tarih or r.d, r.d)
        x = satirlar(r.con, f"WITH {metrikler.BANKA} SELECT * FROM bakiye WHERE hesap_id = $h", {"d": g, "h": hesap_id})
        if not x:
            raise HTTPException(404, "Hesap bulunamadı.")
        return {"hesap_id": hesap_id, "para_birimi": x[0]["para_birimi"], "tarih": g.isoformat(),
                "bakiye": _tl(x[0]["bakiye_kurus"])}

    @app.get("/api/v1/banka/hesaplar/{hesap_id}/hareketler", tags=["Banka"], summary="Hesap hareketlerini listeler")
    def hareketler(hesap_id: str = Path(..., description="Hesap kimliği"),
                   baslangic: dt.date | None = Query(None, description="İşlem tarihi en erken"),
                   bitis: dt.date | None = Query(None, description="İşlem tarihi en geç"),
                   sayfa: int = SAYFA, sayfa_boyutu: int = BOYUT, r: Istek = Depends(istek)):
        r.yalniz_cfo()
        kosul, p = ["hesap_id = $h", "tarih <= $d"], {"h": hesap_id, "d": r.d}
        for alan, deger, ifade in (("bas", baslangic, "tarih >= $bas"), ("bit", bitis, "tarih <= $bit")):
            if deger:
                kosul.append(ifade)
                p[alan] = deger
        sql = f"SELECT * FROM banka_hareketleri WHERE {' AND '.join(kosul)} ORDER BY tarih DESC, hareket_id DESC"
        return sayfala(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "hareket_id": x["hareket_id"], "tarih": _gun(x["tarih"]), "tutar": _tl(x["tutar_kurus"]),
            "bakiye": _tl(x["bakiye_kurus"]), "aciklama": x["aciklama"]})

    return app
