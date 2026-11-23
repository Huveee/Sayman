"""Phase 9: fake ERP and bank API (FastAPI). The generated tools are produced from its OpenAPI definition.

This API imitates an ordinary ERP and open-banking API that a company already has. The design decisions were made not
by looking at the question set but by asking "what does a typical ERP / bank API offer", and were written before the
measurement:

- Record endpoints only: lists (filter + pagination) and single records. There are no computed report endpoints
  (aging, DSO, overdue receivables, cash flow forecast, FX risk): ERPs give these as reports on screen and mostly not
  in the API.
- There are two ready totals, because they are ordinary fields in ERPs and in open banking (ÖHVPS): the current
  account balance of the customer (the sum of the TL equivalents of the open invoices) and the balance of the bank
  account.
- Lists are paginated: `sayfa_boyutu` defaults to 25, at most 100; every response also gives the `toplam_kayit` count.
  The ordering of every list is fixed (by date or due date); there is no ordering by amount.
- Amounts are decimal text in their own currency (e.g. "1234.56"), and so are rates: this is usual in open-banking
  APIs (money is not carried as a floating-point number). Invoices also have the TL equivalent. Explanation: in the
  first draft the amounts were numbers (1234.56); the source scan of the verifier (`sayman.assistant.verifier`) does
  not count floating-point numbers as sources, so every amount in the generated arm would have been "unsourced". The
  API was changed to text (before the measurement) so that the verifier stays the same in both arms.
- Field names follow the data model of the company (musteri_id, musteri, fatura_id ...).
- GET only. No endpoint writes, deletes or moves money.
- Today: the clock of the server (the simulation day). Records after today are not visible at any endpoint.

Permission: the request comes through the API gate (`sayman.experiment.generated_tools.GeneratedServer`); the gate
verifies the identity card and passes the role in the X-Rol header. The API turns the role into a scoped database
connection (the same scope copies as Phase 3) and closes the bank, supplier, payment and POS endpoints to the sales
roles (403). So in both arms permission lives in the data layer.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.security import APIKeyHeader

from sayman.warehouse import metrics
from sayman.warehouse.loader import query_rows
from sayman.tools.catalog import rate_text, lira_text

DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE = 25, 100
SALES_BLOCKED = "Bu rol banka, tedarikçi, ödeme ve POS verisini göremez."


def _to_tl(cents) -> str | None:
    return None if cents is None else lira_text(cents)


def _iso_date(x) -> str | None:
    return x.isoformat() if isinstance(x, (dt.date, dt.datetime)) else x


class ApiEnvironment:
    """Role → (scope, connection) and the server's today."""

    def __init__(self, connections: dict, roles: dict[str, str], today: Callable[[], dt.date]):
        self.connections, self.roller, self.today = connections, roles, today


def create_app(environment: ApiEnvironment) -> FastAPI:
    app = FastAPI(title="Sahte ERP ve Banka API'si", version="1.0.0",
                  description="Müşteri, fatura, tahsilat, çek, tedarikçi, POS ve banka kayıtları (salt okunur).")

    class ApiRequest:
        def __init__(self, role: str):
            if role not in environment.roller:
                raise HTTPException(401, "Rol tanınmadı.")
            self.role, self.scope = role, environment.roller[role]
            self.con, self.d = environment.connections[self.scope], environment.today()

        def cfo_only(self):
            if self.scope != "hepsi":
                raise HTTPException(403, SALES_BLOCKED)

    role_header = APIKeyHeader(name="X-Rol", description="API kapısının doğrulanmış kimlikten koyduğu rol")

    def get_request(x_role: str = Depends(role_header)) -> ApiRequest:
        return ApiRequest(x_role)

    def paginate(r: ApiRequest, sql: str, p: dict, sayfa: int, size: int, convert) -> dict:
        total = query_rows(r.con, f"SELECT count(*) AS n FROM ({sql})", p)[0]["n"]
        listing = query_rows(r.con, sql + " LIMIT $lim OFFSET $ofs", {**p, "lim": size, "ofs": (sayfa - 1) * size})
        return {"toplam_kayit": int(total), "sayfa": sayfa, "sayfa_boyutu": size, "kayitlar": [convert(x) for x in listing]}

    PAGE_QUERY = Query(1, ge=1, description="Sayfa numarası (1'den başlar)")
    PAGE_SIZE_QUERY = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Sayfadaki kayıt sayısı")

    # ------------------------------------------------------------------ customers

    def _customer_json(x):
        return {"musteri_id": x["musteri_id"], "ad": x["ad"], "ulke": x["ulke"], "para_birimi": x["para_birimi"],
                "vade_gun": x["vade_gun"], "ilk_islem_tarihi": _iso_date(x["ilk_islem_tarihi"])}

    @app.get("/api/v1/musteriler", tags=["ERP"], summary="Müşterileri listeler")
    def musteriler(ad: str | None = Query(None, description="Müşteri adında geçen metin"),
                   ulke: str | None = Query(None, description="Ülke adı (ör. Türkiye, Almanya)"),
                   sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        conditions, p = ["ilk_islem_tarihi <= $d"], {"d": r.d}
        if ad:
            conditions.append("lower(ad) LIKE '%' || lower($ad) || '%'")
            p["ad"] = ad
        if ulke:
            conditions.append("ulke = $ulke")
            p["ulke"] = ulke
        sql = f"SELECT * FROM musteriler WHERE {' AND '.join(conditions)} ORDER BY ad, musteri_id"
        return paginate(r, sql, p, sayfa, sayfa_boyutu, _customer_json)

    @app.get("/api/v1/musteriler/{musteri_id}", tags=["ERP"], summary="Müşteri kartı ve cari bakiyesi")
    def musteri(musteri_id: str = Path(..., description="Müşteri kimliği (ör. M00001)"), r: ApiRequest = Depends(get_request)):
        x = query_rows(r.con, "SELECT * FROM musteriler WHERE musteri_id = $m AND ilk_islem_tarihi <= $d",
                     {"m": musteri_id, "d": r.d})
        if not x:
            raise HTTPException(404, "Müşteri bulunamadı.")
        b = query_rows(r.con, f"WITH {metrics.OPEN_INVOICES} SELECT coalesce(sum(acik_try_kurus), 0) AS b, count(*) AS n "
                            "FROM acik_pozitif WHERE musteri_id = $m", {"m": musteri_id, "d": r.d})[0]
        return {**_customer_json(x[0]), "cari_bakiye_tl": _to_tl(b["b"]), "acik_fatura_sayisi": int(b["n"])}

    # ------------------------------------------------------------------ invoices

    def _invoice_json(x):
        f = {"fatura_id": x["fatura_id"], "kaynak_fatura_no": x["kaynak_fatura_no"], "tur": x["tur"],
             "musteri_id": x["musteri_id"], "musteri": x["ad"], "fatura_tarihi": _iso_date(x["fatura_tarihi"]),
             "vade_tarihi": _iso_date(x["vade_tarihi"]), "para_birimi": x["para_birimi"], "tutar": _to_tl(x["tutar_kurus"]),
             "tutar_tl": _to_tl(x["tutar_try_kurus"])}
        if x["tur"] == "satis":
            remaining = max(int(x["acik_kurus"]), 0)
            f.update(kalan_tutar=_to_tl(remaining), durum="acik" if remaining > 0 else "kapali")
        else:
            f.update(iliskili_fatura_id=x["iliskili_fatura_id"], durum="iade")
        return f

    INVOICE_BASE_SQL = f"""
        WITH {metrics.OPEN_INVOICES}
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
                  sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        conditions, p = [], {"d": r.d}
        for param_name, value, condition in (("m", musteri_id, "f.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("no", fatura_no, "(lower(f.fatura_id) = lower($no) OR lower(f.kaynak_fatura_no) = lower($no))"),
                                   ("tur", tur, "f.tur = $tur"), ("bas", baslangic, "f.fatura_tarihi >= $bas"),
                                   ("bit", bitis, "f.fatura_tarihi <= $bit"), ("vb", vade_baslangic, "f.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "f.vade_tarihi <= $vs")):
            if value:
                conditions.append(condition)
                p[param_name] = value.strip() if isinstance(value, str) else value
        if durum == "acik":
            conditions.append("f.tur = 'satis' AND a.acik_kurus > 0")
        elif durum == "kapali":
            conditions.append("f.tur = 'satis' AND a.acik_kurus <= 0")
        order_by = {"tarih_azalan": "f.fatura_tarihi DESC, f.fatura_id DESC", "tarih_artan": "f.fatura_tarihi, f.fatura_id",
                "vade_artan": "f.vade_tarihi, f.fatura_id"}[siralama]
        sql = INVOICE_BASE_SQL + "".join(f" AND {k}" for k in conditions) + f" ORDER BY {order_by}"
        return paginate(r, sql, p, sayfa, sayfa_boyutu, _invoice_json)

    @app.get("/api/v1/faturalar/{fatura_id}", tags=["ERP"], summary="Tek bir faturayı getirir")
    def fatura(fatura_id: str = Path(..., description="Fatura numarası (ör. F000001)"), r: ApiRequest = Depends(get_request)):
        x = query_rows(r.con, INVOICE_BASE_SQL + " AND f.fatura_id = $f", {"d": r.d, "f": fatura_id})
        if not x:
            raise HTTPException(404, "Fatura bulunamadı.")
        return _invoice_json(x[0])

    # ------------------------------------------------------------------ collections and cheques

    @app.get("/api/v1/tahsilatlar", tags=["ERP"], summary="Müşterilerden yapılan tahsilatları listeler")
    def tahsilatlar(musteri_id: str | None = Query(None, description="Müşteri kimliği"),
                    musteri_adi: str | None = Query(None, description="Müşteri adında geçen metin"),
                    baslangic: dt.date | None = Query(None, description="Tahsilat tarihi en erken"),
                    bitis: dt.date | None = Query(None, description="Tahsilat tarihi en geç"),
                    yontem: Literal["havale", "swift", "cek"] | None = Query(None, description="Ödeme yöntemi"),
                    sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        conditions, p = ["t.tarih <= $d"], {"d": r.d}
        for param_name, value, condition in (("m", musteri_id, "t.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("bas", baslangic, "t.tarih >= $bas"), ("bit", bitis, "t.tarih <= $bit"),
                                   ("y", yontem, "t.yontem = $y")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        sql = (f"SELECT t.*, m.ad FROM tahsilatlar t LEFT JOIN musteriler m ON m.musteri_id = t.musteri_id "
               f"WHERE {' AND '.join(conditions)} ORDER BY t.tarih DESC, t.tahsilat_id DESC")
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "tahsilat_id": x["tahsilat_id"], "musteri_id": x["musteri_id"], "musteri": x["ad"],
            "fatura_id": x["fatura_id"], "tarih": _iso_date(x["tarih"]), "para_birimi": x["para_birimi"],
            "tutar": _to_tl(x["tutar_kurus"]), "yontem": x["yontem"], "cek_id": x["cek_id"]})

    @app.get("/api/v1/cekler", tags=["ERP"], summary="Müşterilerden alınan çekleri listeler")
    def cekler(musteri_id: str | None = Query(None, description="Müşteri kimliği"),
               musteri_adi: str | None = Query(None, description="Müşteri adında geçen metin"),
               durum: Literal["portfoyde", "tahsilde", "tahsil_edildi", "karsiliksiz"] | None = Query(
                   None, description="Çekin bugünkü durumu"),
               vade_baslangic: dt.date | None = Query(None, description="Vade tarihi en erken"),
               vade_bitis: dt.date | None = Query(None, description="Vade tarihi en geç"),
               sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        conditions, p = [], {"d": r.d}
        for param_name, value, condition in (("m", musteri_id, "c.musteri_id = $m"),
                                   ("ad", musteri_adi, "lower(m.ad) LIKE '%' || lower($ad) || '%'"),
                                   ("du", durum, "son.durum = $du"), ("vb", vade_baslangic, "c.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "c.vade_tarihi <= $vs")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        sql = f"""
            WITH son AS (SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC,
                                cek_hareket_id DESC) AS s FROM cek_hareketleri WHERE tarih <= $d)
            SELECT c.*, m.ad, son.durum FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
            LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
            WHERE c.alis_tarihi <= $d{''.join(f' AND {k}' for k in conditions)} ORDER BY c.vade_tarihi, c.cek_id"""
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "cek_id": x["cek_id"], "cek_no": x["cek_no"], "musteri_id": x["musteri_id"], "musteri": x["ad"],
            "kesideci_banka": x["kesideci_banka"], "alis_tarihi": _iso_date(x["alis_tarihi"]),
            "vade_tarihi": _iso_date(x["vade_tarihi"]), "tutar": _to_tl(x["tutar_kurus"]), "durum": x["durum"]})

    # ------------------------------------------------------------------ suppliers and payments (CFO only)

    @app.get("/api/v1/tedarikciler", tags=["ERP"], summary="Tedarikçileri listeler")
    def tedarikciler(ad: str | None = Query(None, description="Tedarikçi adında geçen metin"),
                     sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        conditions, p = "", {}
        if ad:
            conditions, p = " WHERE lower(ad) LIKE '%' || lower($ad) || '%'", {"ad": ad}
        return paginate(r, f"SELECT * FROM tedarikciler{conditions} ORDER BY ad, tedarikci_id", p, sayfa, sayfa_boyutu,
                       lambda x: {k: x[k] for k in ("tedarikci_id", "ad", "kategori", "vade_gun")})

    @app.get("/api/v1/alis-faturalari", tags=["ERP"], summary="Tedarikçi (alış) faturalarını listeler")
    def alis_faturalari(tedarikci_id: str | None = Query(None, description="Tedarikçi kimliği"),
                        durum: Literal["acik", "kapali"] | None = Query(None, description="Ödenme durumu"),
                        vade_baslangic: dt.date | None = Query(None, description="Vade tarihi en erken"),
                        vade_bitis: dt.date | None = Query(None, description="Vade tarihi en geç"),
                        sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        conditions, p = [], {"d": r.d}
        for param_name, value, condition in (("t", tedarikci_id, "b.tedarikci_id = $t"), ("vb", vade_baslangic, "b.vade_tarihi >= $vb"),
                                   ("vs", vade_bitis, "b.vade_tarihi <= $vs")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        if durum:
            conditions.append("b.acik_kurus > 0" if durum == "acik" else "b.acik_kurus <= 0")
        sql = (f"WITH {metrics.OPEN_PAYABLES} SELECT b.*, t.ad, a.tutar_kurus FROM acik_borc b "
               f"JOIN alis_faturalari a ON a.alis_fatura_id = b.alis_fatura_id "
               f"LEFT JOIN tedarikciler t ON t.tedarikci_id = b.tedarikci_id"
               f"{' WHERE ' + ' AND '.join(conditions) if conditions else ''} ORDER BY b.vade_tarihi, b.alis_fatura_id")
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "alis_fatura_id": x["alis_fatura_id"], "tedarikci_id": x["tedarikci_id"], "tedarikci": x["ad"],
            "kategori": x["kategori"], "tarih": _iso_date(x["tarih"]), "vade_tarihi": _iso_date(x["vade_tarihi"]),
            "tutar": _to_tl(x["tutar_kurus"]), "kalan_tutar": _to_tl(max(int(x["acik_kurus"]), 0))})

    @app.get("/api/v1/odemeler", tags=["ERP"], summary="Yapılan ödemeleri listeler")
    def odemeler(tedarikci_id: str | None = Query(None, description="Tedarikçi kimliği"),
                 baslangic: dt.date | None = Query(None, description="Ödeme tarihi en erken"),
                 bitis: dt.date | None = Query(None, description="Ödeme tarihi en geç"),
                 sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        conditions, p = ["tarih <= $d"], {"d": r.d}
        for param_name, value, condition in (("t", tedarikci_id, "tedarikci_id = $t"), ("bas", baslangic, "tarih >= $bas"),
                                   ("bit", bitis, "tarih <= $bit")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        sql = f"SELECT * FROM odemeler WHERE {' AND '.join(conditions)} ORDER BY tarih DESC, odeme_id DESC"
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "odeme_id": x["odeme_id"], "tur": x["tur"], "tedarikci_id": x["tedarikci_id"],
            "alis_fatura_id": x["alis_fatura_id"], "tarih": _iso_date(x["tarih"]), "tutar": _to_tl(x["tutar_kurus"]),
            "yontem": x["yontem"], "aciklama": x["aciklama"]})

    @app.get("/api/v1/pos-islemleri", tags=["ERP"], summary="POS (kartlı satış) işlemlerini listeler")
    def pos_islemleri(baslangic: dt.date | None = Query(None, description="İşlem tarihi en erken"),
                      bitis: dt.date | None = Query(None, description="İşlem tarihi en geç"),
                      sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        conditions, p = ["islem_tarihi <= $d"], {"d": r.d}
        for param_name, value, condition in (("bas", baslangic, "islem_tarihi >= $bas"), ("bit", bitis, "islem_tarihi <= $bit")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        sql = f"SELECT * FROM pos_islemleri WHERE {' AND '.join(conditions)} ORDER BY islem_tarihi DESC, pos_id DESC"
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "pos_id": x["pos_id"], "islem_tarihi": _iso_date(x["islem_tarihi"]), "tutar": _to_tl(x["tutar_kurus"]),
            "komisyon": _to_tl(x["komisyon_kurus"]), "valor_tarihi": _iso_date(x["valor_tarihi"]),
            "kaynak_fatura_no": x["kaynak_fatura_no"]})

    # ------------------------------------------------------------------ rates (open to everyone)

    @app.get("/api/v1/kurlar", tags=["ERP"], summary="TCMB döviz alış kuru")
    def kurlar(para_birimi: Literal["USD", "EUR", "GBP"] = Query(..., description="Para birimi"),
               tarih: dt.date | None = Query(None, description="Gün (verilmezse bugün); o gün yoksa önceki iş günü"),
               r: ApiRequest = Depends(get_request)):
        g = min(tarih or r.d, r.d)
        x = query_rows(r.con, "SELECT tarih, alis FROM doviz_kurlari WHERE para_birimi = $pb AND tarih <= $g "
                            "ORDER BY tarih DESC LIMIT 1", {"pb": para_birimi, "g": g})
        if not x:
            raise HTTPException(404, "Kur bulunamadı.")
        return {"para_birimi": para_birimi, "tarih": _iso_date(x[0]["tarih"]), "alis": rate_text(x[0]["alis"])}

    # ------------------------------------------------------------------ bank (open-banking style, CFO only)

    @app.get("/api/v1/banka/hesaplar", tags=["Banka"], summary="Banka hesaplarını listeler")
    def hesaplar(r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        listing = query_rows(r.con, "SELECT * FROM banka_hesaplari WHERE acilis_tarihi <= $d ORDER BY hesap_id", {"d": r.d})
        return {"hesaplar": [{"hesap_id": x["hesap_id"], "banka": x["banka"], "para_birimi": x["para_birimi"],
                              "acilis_tarihi": _iso_date(x["acilis_tarihi"])} for x in listing]}

    @app.get("/api/v1/banka/hesaplar/{hesap_id}/bakiye", tags=["Banka"], summary="Hesap bakiyesi")
    def bakiye(hesap_id: str = Path(..., description="Hesap kimliği (ör. A_TRY)"),
               tarih: dt.date | None = Query(None, description="Gün sonu (verilmezse bugün)"),
               r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        g = min(tarih or r.d, r.d)
        x = query_rows(r.con, f"WITH {metrics.BANK} SELECT * FROM bakiye WHERE hesap_id = $h", {"d": g, "h": hesap_id})
        if not x:
            raise HTTPException(404, "Hesap bulunamadı.")
        return {"hesap_id": hesap_id, "para_birimi": x[0]["para_birimi"], "tarih": g.isoformat(),
                "bakiye": _to_tl(x[0]["bakiye_kurus"])}

    @app.get("/api/v1/banka/hesaplar/{hesap_id}/hareketler", tags=["Banka"], summary="Hesap hareketlerini listeler")
    def hareketler(hesap_id: str = Path(..., description="Hesap kimliği"),
                   baslangic: dt.date | None = Query(None, description="İşlem tarihi en erken"),
                   bitis: dt.date | None = Query(None, description="İşlem tarihi en geç"),
                   sayfa: int = PAGE_QUERY, sayfa_boyutu: int = PAGE_SIZE_QUERY, r: ApiRequest = Depends(get_request)):
        r.cfo_only()
        conditions, p = ["hesap_id = $h", "tarih <= $d"], {"h": hesap_id, "d": r.d}
        for param_name, value, condition in (("bas", baslangic, "tarih >= $bas"), ("bit", bitis, "tarih <= $bit")):
            if value:
                conditions.append(condition)
                p[param_name] = value
        sql = f"SELECT * FROM banka_hareketleri WHERE {' AND '.join(conditions)} ORDER BY tarih DESC, hareket_id DESC"
        return paginate(r, sql, p, sayfa, sayfa_boyutu, lambda x: {
            "hareket_id": x["hareket_id"], "tarih": _iso_date(x["tarih"]), "tutar": _to_tl(x["tutar_kurus"]),
            "bakiye": _to_tl(x["bakiye_kurus"]), "aciklama": x["aciklama"]})

    return app
