"""Finans metrikleri: ambardaki tablolardan, verilen gün sonu itibarıyla (as-of) SQL ile hesaplanır.

Her metrik bir `tarih` alır ve yalnızca o güne kadar gerçekleşmiş kayıtları kullanır; tarihi sonra olan
hiçbir satır sonuca girmez. Tutarlar tamsayı kuruştur. Döviz tutarlarının TL karşılığı, eşitlikte çift
tarafa yuvarlanır (sayman.veri.para.kurusa ile aynı kural).

Tanımlar (D = tarih):
- Açık fatura: satış faturası (fatura_tarihi <= D), tutar - D'ye kadarki tahsilatlar - D'ye kadarki bağlı
  iade faturalarının uygulanan kısmı > 0. Çek, tahsil edilene kadar faturayı kapatmaz (Faz 1 kuralı).
- Eşleşmemiş tahsilat: faturaya bağlanmamış tahsilat; müşterinin alacağından düşülür.
- Kur: D'den önceki (D dahil) en son TCMB alış kuru. Kur yoksa hata verilir; sessizce atlanmaz.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal

from sayman.ambar.kurulum import satirlar

DILIMLER = (  # (sütun, en az gecikme günü, en çok gecikme günü); None = sınırsız
    ("vadesi_gelmemis", None, 0),
    ("gecikme_1_30", 1, 30),
    ("gecikme_31_60", 31, 60),
    ("gecikme_61_90", 61, 90),
    ("gecikme_91_180", 91, 180),
    ("gecikme_180_ustu", 181, None),
)
SUPHELI_GUN = 90  # nakit tahmininde: vadesini bundan fazla geçmiş alacak, tahsil tarihi belirsiz sayılır
GECMIS_PENCERESI = 365  # nakit tahmininde: müşterinin ortalama gecikmesi son bu kadar günde kapanan faturalardan
DAHIL_OLMAYANLAR = (
    "Henüz kesilmemiş satışlar ve onlardan gelecek tahsilatlar (peşin satışlar ve POS dahil)",
    "Maaş, SGK ve vergi ödemeleri ile alış faturası olmayan diğer ödemeler",
    "Müşterilere yapılacak iade ödemeleri ve banka masrafları",
    "Hesaplar arası virman ve döviz bozdurma (şirketin toplam nakdini değiştirmez)",
)


def _kur(alan: str, para_birimi: str) -> str:
    return f"CASE WHEN {alan} IS NULL THEN error('kur bulunamadı: ' || {para_birimi}) ELSE {alan} END"


KUR_D = """
kur_d AS (
    SELECT para_birimi, arg_max(alis, tarih) AS kur FROM doviz_kurlari WHERE tarih <= $d GROUP BY para_birimi
    UNION ALL SELECT 'TRY', CAST(1 AS DECIMAL(18,6))
)"""

ACIK = """
tah AS (
    SELECT fatura_id, sum(tutar_kurus) AS s FROM tahsilatlar
    WHERE tarih <= $d AND fatura_id IS NOT NULL GROUP BY fatura_id
),
iad AS (
    SELECT iliskili_fatura_id AS fatura_id, sum(coalesce(uygulanan_kurus, 0)) AS s FROM faturalar
    WHERE tur = 'iade' AND fatura_tarihi <= $d AND iliskili_fatura_id IS NOT NULL GROUP BY iliskili_fatura_id
),
acik AS (
    SELECT f.fatura_id, f.musteri_id, f.fatura_tarihi, f.vade_tarihi, f.para_birimi, f.kur,
           f.tutar_kurus - coalesce(tah.s, 0) - coalesce(iad.s, 0) AS acik_kurus,
           date_diff('day', f.vade_tarihi, CAST($d AS DATE)) AS gecikme_gun
    FROM faturalar f
    LEFT JOIN tah ON tah.fatura_id = f.fatura_id
    LEFT JOIN iad ON iad.fatura_id = f.fatura_id
    WHERE f.tur = 'satis' AND f.fatura_tarihi <= $d
),
acik_pozitif AS (
    SELECT *, kur_carp(acik_kurus, kur) AS acik_try_kurus FROM acik WHERE acik_kurus > 0
)"""

ESLESMEMIS = f"""
eslesmemis AS (
    SELECT t.tahsilat_id, t.musteri_id, t.para_birimi, t.tutar_kurus,
           kur_carp(t.tutar_kurus, CASE WHEN t.para_birimi = 'TRY' THEN CAST(1 AS DECIMAL(18,6))
                                        ELSE {_kur('k.alis', 't.para_birimi')} END) AS try_kurus
    FROM tahsilatlar t
    ASOF LEFT JOIN doviz_kurlari k ON k.para_birimi = t.para_birimi AND k.tarih <= t.tarih
    WHERE t.fatura_id IS NULL AND t.tarih <= $d
)"""

ACIK_BORC = """
od AS (
    SELECT alis_fatura_id, sum(tutar_kurus) AS s FROM odemeler
    WHERE tarih <= $d AND alis_fatura_id IS NOT NULL GROUP BY alis_fatura_id
),
acik_borc AS (
    SELECT a.alis_fatura_id, a.tedarikci_id, a.kategori, a.tarih, a.vade_tarihi,
           a.tutar_kurus - coalesce(od.s, 0) AS acik_kurus
    FROM alis_faturalari a LEFT JOIN od ON od.alis_fatura_id = a.alis_fatura_id
    WHERE a.tarih <= $d
)"""

BANKA = """
har AS (SELECT hesap_id, sum(tutar_kurus) AS s FROM banka_hareketleri WHERE tarih <= $d GROUP BY hesap_id),
bakiye AS (
    SELECT h.hesap_id, h.banka, h.para_birimi, h.acilis_bakiye_kurus + coalesce(har.s, 0) AS bakiye_kurus
    FROM banka_hesaplari h LEFT JOIN har ON har.hesap_id = h.hesap_id
    WHERE h.acilis_tarihi <= $d
)"""


def _gun(tarih) -> dt.date:
    if isinstance(tarih, dt.datetime) or not isinstance(tarih, dt.date):
        raise TypeError("tarih bir datetime.date olmalı")
    return tarih


def _tamsayi(satir: dict) -> dict:
    """DuckDB'nin HUGEINT toplamları Python int olarak gelir; Decimal kalan tamsayıları int'e çevirir."""
    return {k: (int(v) if isinstance(v, Decimal) and k.endswith(("_kurus", "_gun")) else v) for k, v in satir.items()}


# ---------------------------------------------------------------- 1. nakit pozisyonu


def nakit_pozisyonu(con, tarih: dt.date) -> dict:
    """Her banka hesabının D gün sonu bakiyesi (açılış + D'ye kadarki hareketler) ve TL karşılığı."""
    sql = f"""
    WITH {KUR_D}, {BANKA}
    SELECT b.hesap_id, b.banka, b.para_birimi, b.bakiye_kurus, {_kur('k.kur', 'b.para_birimi')} AS kur,
           kur_carp(b.bakiye_kurus, {_kur('k.kur', 'b.para_birimi')}) AS bakiye_try_kurus
    FROM bakiye b LEFT JOIN kur_d k ON k.para_birimi = b.para_birimi
    ORDER BY b.hesap_id"""
    hesaplar = [_tamsayi(s) for s in satirlar(con, sql, {"d": _gun(tarih)})]
    return {"tarih": tarih, "hesaplar": hesaplar, "toplam_try_kurus": sum(h["bakiye_try_kurus"] for h in hesaplar)}


# ---------------------------------------------------------------- 2. alacak yaşlandırma


def _dilim_case(alan: str) -> str:
    parcalar = []
    for ad, alt, ust in DILIMLER:
        if ust is None:
            parcalar.append(f"ELSE '{ad}'")
        else:
            parcalar.append(f"WHEN {alan} <= {ust} THEN '{ad}'")
    return "CASE " + " ".join(parcalar) + " END"


def yaslandirma(con, tarih: dt.date) -> dict:
    """Müşteri başına açık alacak (TL, fatura kuruyla), vadeden geçen güne göre dilimlere ayrılmış.
    Eşleşmemiş tahsilatlar ayrı bir sütunda eksi olarak durur; toplam = dilimler + eşleşmemiş."""
    dilim_sutunlari = ",\n".join(
        f"sum(CASE WHEN x.dilim = '{ad}' THEN x.tutar ELSE 0 END) AS {ad}_kurus" for ad, _, _ in DILIMLER)
    sql = f"""
    WITH {ACIK}, {ESLESMEMIS},
    x AS (
        SELECT musteri_id, {_dilim_case('gecikme_gun')} AS dilim, acik_try_kurus AS tutar FROM acik_pozitif
        UNION ALL
        SELECT musteri_id, 'eslesmemis_tahsilat', -try_kurus FROM eslesmemis
    )
    SELECT x.musteri_id, any_value(m.ad) AS ad,
           {dilim_sutunlari},
           sum(CASE WHEN x.dilim = 'eslesmemis_tahsilat' THEN x.tutar ELSE 0 END) AS eslesmemis_tahsilat_kurus,
           sum(x.tutar) AS toplam_kurus
    FROM x LEFT JOIN musteriler m ON m.musteri_id = x.musteri_id
    GROUP BY x.musteri_id
    ORDER BY toplam_kurus DESC, x.musteri_id"""
    musteriler = [_tamsayi(s) for s in satirlar(con, sql, {"d": _gun(tarih)})]
    sutunlar = [f"{ad}_kurus" for ad, _, _ in DILIMLER] + ["eslesmemis_tahsilat_kurus", "toplam_kurus"]
    toplam = {s: sum(m[s] for m in musteriler) for s in sutunlar}
    return {"tarih": tarih, "musteriler": musteriler, "toplam": toplam}


# ---------------------------------------------------------------- 3. DSO


def dso_gunu(alacak_kurus: int, satis_kurus: int, gun: int) -> Decimal | None:
    """gun x alacak / satış, 2 haneye eşitlikte çift tarafa yuvarlanmış. Satış yoksa (ya da eksiyse) None."""
    if satis_kurus <= 0:
        return None
    return (Decimal(gun * alacak_kurus) / Decimal(satis_kurus)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def net_satis(con, bas: dt.date, bit: dt.date) -> int:
    """Satış ve iade faturalarının TL tutarı ile POS satışlarının toplamı (bas ve bit dahil)."""
    sql = """
    SELECT coalesce((SELECT sum(tutar_try_kurus) FROM faturalar WHERE fatura_tarihi BETWEEN $bas AND $bit), 0)
         + coalesce((SELECT sum(tutar_kurus) FROM pos_islemleri WHERE islem_tarihi BETWEEN $bas AND $bit), 0) AS s"""
    return int(satirlar(con, sql, {"bas": bas, "bit": bit})[0]["s"])


def dso(con, tarih: dt.date, gun: int = 365) -> dict:
    """Alacak tahsil süresi: gun x (D'deki açık alacak) / (D'de biten `gun` günlük net satış).
    D = 31 Aralık ve gun = 365 iken Faz 1'deki yıllık DSO tanımıyla aynıdır."""
    tarih = _gun(tarih)
    alacak = yaslandirma(con, tarih)["toplam"]["toplam_kurus"]
    satis = net_satis(con, tarih - dt.timedelta(days=gun - 1), tarih)
    return {"tarih": tarih, "gun": gun, "alacak_kurus": alacak, "satis_kurus": satis,
            "dso_gun": dso_gunu(alacak, satis, gun)}


# ---------------------------------------------------------------- 4. geciken alacaklar


def geciken_alacaklar(con, tarih: dt.date, en_az_gun: int = 1) -> dict:
    """Vadesini en az `en_az_gun` gün geçmiş açık faturalar, TL tutarı büyükten küçüğe."""
    sql = f"""
    WITH {ACIK}
    SELECT a.fatura_id, a.musteri_id, m.ad, a.fatura_tarihi, a.vade_tarihi, a.gecikme_gun, a.para_birimi,
           a.acik_kurus, a.acik_try_kurus
    FROM acik_pozitif a LEFT JOIN musteriler m ON m.musteri_id = a.musteri_id
    WHERE a.gecikme_gun >= $en_az
    ORDER BY a.acik_try_kurus DESC, a.fatura_id"""
    faturalar = [_tamsayi(s) for s in satirlar(con, sql, {"d": _gun(tarih), "en_az": en_az_gun})]
    return {"tarih": tarih, "en_az_gun": en_az_gun, "faturalar": faturalar,
            "toplam_try_kurus": sum(f["acik_try_kurus"] for f in faturalar)}


# ---------------------------------------------------------------- 5. yaklaşan ödemeler


def yaklasan_odemeler(con, tarih: dt.date, gun: int = 30) -> dict:
    """Ödenmemiş alış faturalarından vadesi D + gun'e kadar olanlar (vadesi geçmiş ödenmemişler dahil).
    Açık = tutar - D'ye kadarki ödemeler; fazla ödenmiş (mükerrer) fatura eksi açıkla listeye girmez."""
    sql = f"""
    WITH {ACIK_BORC}
    SELECT b.alis_fatura_id, b.tedarikci_id, t.ad, b.kategori, b.tarih, b.vade_tarihi,
           date_diff('day', CAST($d AS DATE), b.vade_tarihi) AS kalan_gun, b.acik_kurus
    FROM acik_borc b LEFT JOIN tedarikciler t ON t.tedarikci_id = b.tedarikci_id
    WHERE b.acik_kurus > 0 AND b.vade_tarihi <= CAST($d AS DATE) + CAST($gun AS INTEGER)
    ORDER BY b.vade_tarihi, b.alis_fatura_id"""
    odemeler = [_tamsayi(s) for s in satirlar(con, sql, {"d": _gun(tarih), "gun": gun})]
    return {"tarih": tarih, "gun": gun, "odemeler": odemeler, "toplam_kurus": sum(o["acik_kurus"] for o in odemeler)}


# ---------------------------------------------------------------- 6. kur riski


def kur_riski(con, tarih: dt.date) -> dict:
    """Her döviz için açık pozisyon: döviz alacak (açık faturalar - eşleşmemiş döviz tahsilatı) + döviz
    banka bakiyesi; D kuruyla TL karşılığı, kur %10 değişirse TL etkisi ve açık alacakların fatura kuruna
    göre kur farkı (D kuruyla TL - fatura kuruyla TL)."""
    sql = f"""
    WITH {KUR_D}, {ACIK}, {BANKA},
    pb AS (
        SELECT para_birimi FROM musteriler WHERE ilk_islem_tarihi <= $d AND para_birimi <> 'TRY'
        UNION SELECT para_birimi FROM banka_hesaplari WHERE acilis_tarihi <= $d AND para_birimi <> 'TRY'
    ),
    alacak AS (
        SELECT a.para_birimi, sum(a.acik_kurus) AS doviz,
               sum(kur_carp(a.acik_kurus, {_kur('k.kur', 'a.para_birimi')}) - kur_carp(a.acik_kurus, a.kur)) AS fark
        FROM acik_pozitif a LEFT JOIN kur_d k ON k.para_birimi = a.para_birimi
        WHERE a.para_birimi <> 'TRY' GROUP BY a.para_birimi
    ),
    esl AS (
        SELECT para_birimi, sum(tutar_kurus) AS s FROM tahsilatlar
        WHERE fatura_id IS NULL AND tarih <= $d GROUP BY para_birimi
    ),
    banka AS (SELECT para_birimi, sum(bakiye_kurus) AS s FROM bakiye GROUP BY para_birimi),
    poz AS (
        SELECT pb.para_birimi, coalesce(alacak.doviz, 0) - coalesce(esl.s, 0) AS alacak_doviz_kurus,
               coalesce(banka.s, 0) AS banka_doviz_kurus, coalesce(alacak.fark, 0) AS alacak_kur_farki_kurus,
               {_kur('k.kur', 'pb.para_birimi')} AS kur
        FROM pb
        LEFT JOIN alacak ON alacak.para_birimi = pb.para_birimi
        LEFT JOIN esl ON esl.para_birimi = pb.para_birimi
        LEFT JOIN banka ON banka.para_birimi = pb.para_birimi
        LEFT JOIN kur_d k ON k.para_birimi = pb.para_birimi
    )
    SELECT para_birimi, alacak_doviz_kurus, banka_doviz_kurus,
           alacak_doviz_kurus + banka_doviz_kurus AS toplam_doviz_kurus, kur,
           kur_carp(alacak_doviz_kurus + banka_doviz_kurus, kur) AS toplam_try_kurus,
           yuvarla_cift(CAST(alacak_doviz_kurus + banka_doviz_kurus AS DECIMAL(38,0)) * kur
                        * CAST(0.1 AS DECIMAL(2,1))) AS yuzde_10_etki_kurus,
           alacak_kur_farki_kurus
    FROM poz ORDER BY para_birimi"""
    dovizler = [_tamsayi(s) for s in satirlar(con, sql, {"d": _gun(tarih)})]
    return {"tarih": tarih, "dovizler": dovizler, "toplam_try_kurus": sum(d["toplam_try_kurus"] for d in dovizler)}


# ---------------------------------------------------------------- 7. nakit akış tahmini


def nakit_akis_tahmini(con, tarih: dt.date, hafta: int = 13) -> dict:
    """D'den sonraki `hafta` haftanın bilinen nakit girişleri ve çıkışları (hafta k: D+7(k-1)+1 ... D+7k).

    Girişler:
    - Açık alacaklar: beklenen gün = vade + müşterinin ortalama gecikmesi (D'den önceki 365 günde kapanan
      faturalarından, gün olarak aşağı yuvarlanmış; geçmişi yoksa şirket ortalaması), en erken D+1.
      Vadesini 90 günden fazla geçmiş alacak tahmine girmez, "şüpheli" olarak ayrıca verilir.
      Döviz alacak D kuruyla TL'ye çevrilir.
    - Portföydeki ve tahsildeki çekler: çek vadesinde. Müşterinin bekleyen çekleri ve eşleşmemiş
      tahsilatları, aynı parayı iki kez saymamak için o müşterinin en eski vadeli açık faturalarını kapatmış
      sayılır.
    - Valörü gelmemiş POS satışları: valör gününde, komisyon düşülmüş.
    Çıkışlar:
    - Ödenmemiş alış faturaları: vadede, vadesi geçmişse D+1'de.
    Dahil olmayanlar DAHIL_OLMAYANLAR'da; açılış = nakit pozisyonunun TL toplamı.

    "bilinen_kalemlerle_bakiye_kurus" bir bakiye tahmini değildir: maaş, vergi ve yeni satışlar gibi
    dahil olmayan kalemler girmediği için gerçek bakiye bundan farklı olur. Vadesi geçmiş (en çok 90 gün)
    ve beklenen günü geçmiş alacaklar 1. haftaya yazılır; bu yüzden 1. hafta girişleri sonrakilerden büyüktür.
    """
    tarih = _gun(tarih)
    sql = f"""
    WITH {KUR_D}, {ACIK}, {ACIK_BORC},
    cek_son AS (
        SELECT cek_id, durum FROM (
            SELECT cek_id, durum,
                   row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS sira
            FROM cek_hareketleri WHERE tarih <= $d)
        WHERE sira = 1
    ),
    bekleyen_cek AS (
        SELECT c.cek_id, c.musteri_id, c.vade_tarihi, c.tutar_kurus
        FROM cekler c JOIN cek_son s ON s.cek_id = c.cek_id
        WHERE c.alis_tarihi <= $d AND s.durum IN ('portfoyde', 'tahsilde')
    ),
    havuz AS (
        SELECT musteri_id, sum(tutar_kurus) AS havuz_kurus FROM (
            SELECT musteri_id, tutar_kurus FROM bekleyen_cek
            UNION ALL
            SELECT musteri_id, tutar_kurus FROM tahsilatlar WHERE fatura_id IS NULL AND tarih <= $d)
        GROUP BY musteri_id
    ),
    sirali AS (
        SELECT a.*, coalesce(sum(a.acik_kurus) OVER (
                   PARTITION BY a.musteri_id ORDER BY a.vade_tarihi, a.fatura_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS onceki_kurus
        FROM acik_pozitif a
    ),
    kalan AS (
        SELECT s.*, s.acik_kurus - least(s.acik_kurus, greatest(0, coalesce(h.havuz_kurus, 0) - s.onceki_kurus))
                    AS kalan_kurus
        FROM sirali s LEFT JOIN havuz h ON h.musteri_id = s.musteri_id
    ),
    olgu AS (
        SELECT fatura_id, tarih, tutar_kurus AS tutar FROM tahsilatlar WHERE fatura_id IS NOT NULL AND tarih <= $d
        UNION ALL
        SELECT iliskili_fatura_id, fatura_tarihi, coalesce(uygulanan_kurus, 0) FROM faturalar
        WHERE tur = 'iade' AND iliskili_fatura_id IS NOT NULL AND fatura_tarihi <= $d
    ),
    kumulatif AS (
        SELECT fatura_id, tarih, sum(sum(tutar)) OVER (PARTITION BY fatura_id ORDER BY tarih) AS toplam
        FROM olgu GROUP BY fatura_id, tarih
    ),
    ilk_kapanis AS (
        SELECT k.fatura_id, min(k.tarih) AS gun
        FROM kumulatif k JOIN faturalar f ON f.fatura_id = k.fatura_id
        WHERE k.toplam >= f.tutar_kurus GROUP BY k.fatura_id
    ),
    kapanis AS (
        SELECT f.musteri_id, f.vade_tarihi,
               CASE WHEN f.tutar_kurus <= 0 THEN f.fatura_tarihi ELSE ik.gun END AS kapanis_tarihi
        FROM faturalar f LEFT JOIN ilk_kapanis ik ON ik.fatura_id = f.fatura_id
        WHERE f.tur = 'satis' AND f.fatura_tarihi <= $d
    ),
    gecmis AS (
        SELECT musteri_id, greatest(0, date_diff('day', vade_tarihi, kapanis_tarihi)) AS gecikme
        FROM kapanis
        WHERE kapanis_tarihi BETWEEN CAST($d AS DATE) - CAST($pencere AS INTEGER) + 1 AND $d
    ),
    musteri_ort AS (SELECT musteri_id, CAST(sum(gecikme) // count(*) AS INTEGER) AS ort FROM gecmis GROUP BY musteri_id),
    sirket_ort AS (SELECT CAST(coalesce(sum(gecikme) // count(*), 0) AS INTEGER) AS ort FROM gecmis),
    kalemler AS (
        SELECT 'alacak' AS tur,
               CASE WHEN k.gecikme_gun > $supheli THEN NULL
                    ELSE greatest(k.vade_tarihi + coalesce(mo.ort, so.ort), CAST($d AS DATE) + 1) END AS gun,
               kur_carp(k.kalan_kurus, {_kur('kd.kur', 'k.para_birimi')}) AS tutar
        FROM kalan k
        LEFT JOIN musteri_ort mo ON mo.musteri_id = k.musteri_id
        CROSS JOIN sirket_ort so
        LEFT JOIN kur_d kd ON kd.para_birimi = k.para_birimi
        WHERE k.kalan_kurus > 0
        UNION ALL
        SELECT 'cek', greatest(c.vade_tarihi, CAST($d AS DATE) + 1),
               kur_carp(c.tutar_kurus, {_kur('kd.kur', 'm.para_birimi')})
        FROM bekleyen_cek c
        LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
        LEFT JOIN kur_d kd ON kd.para_birimi = m.para_birimi
        UNION ALL
        SELECT 'pos', valor_tarihi, tutar_kurus - komisyon_kurus FROM pos_islemleri
        WHERE islem_tarihi <= $d AND valor_tarihi > $d
        UNION ALL
        SELECT 'tedarikci', greatest(vade_tarihi, CAST($d AS DATE) + 1), acik_kurus FROM acik_borc WHERE acik_kurus > 0
    )
    SELECT tur,
           CASE WHEN gun IS NULL THEN -1
                WHEN (date_diff('day', CAST($d AS DATE), gun) - 1) // 7 + 1 > $hafta THEN 0
                ELSE (date_diff('day', CAST($d AS DATE), gun) - 1) // 7 + 1 END AS hafta,
           sum(tutar) AS tutar
    FROM kalemler GROUP BY ALL ORDER BY tur, hafta"""
    parametreler = {"d": tarih, "hafta": hafta, "supheli": SUPHELI_GUN, "pencere": GECMIS_PENCERESI}
    toplamlar = {(s["tur"], s["hafta"]): int(s["tutar"]) for s in satirlar(con, sql, parametreler)}

    acilis = nakit_pozisyonu(con, tarih)["toplam_try_kurus"]
    haftalar, bakiye = [], acilis
    for k in range(1, hafta + 1):
        alacak, cek, pos, tedarikci = (toplamlar.get((tur, k), 0) for tur in ("alacak", "cek", "pos", "tedarikci"))
        net = alacak + cek + pos - tedarikci
        bakiye += net
        haftalar.append({
            "hafta": k, "bas": tarih + dt.timedelta(days=7 * (k - 1) + 1), "bit": tarih + dt.timedelta(days=7 * k),
            "alacak_girisi_kurus": alacak, "cek_girisi_kurus": cek, "pos_girisi_kurus": pos,
            "tedarikci_cikisi_kurus": tedarikci, "net_kurus": net, "bilinen_kalemlerle_bakiye_kurus": bakiye,
        })
    return {
        "tarih": tarih, "hafta_sayisi": hafta, "acilis_kurus": acilis, "haftalar": haftalar,
        "ufuk_disi_giris_kurus": sum(toplamlar.get((tur, 0), 0) for tur in ("alacak", "cek", "pos")),
        "ufuk_disi_cikis_kurus": toplamlar.get(("tedarikci", 0), 0),
        "supheli_alacak_kurus": toplamlar.get(("alacak", -1), 0),
        "dahil_olmayanlar": list(DAHIL_OLMAYANLAR),
    }


METRIKLER = {
    "nakit_pozisyonu": nakit_pozisyonu,
    "yaslandirma": yaslandirma,
    "dso": dso,
    "geciken_alacaklar": geciken_alacaklar,
    "yaklasan_odemeler": yaklasan_odemeler,
    "kur_riski": kur_riski,
    "nakit_akis_tahmini": nakit_akis_tahmini,
}
