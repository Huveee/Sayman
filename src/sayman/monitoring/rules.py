"""İzleme kuralları: her biri veriye bakıp "bugün yeni başlayan bir sorun var mı" diye karar verir.

Her kural bir güne kadar olan kayıtlarla çalışır; o günden sonrasını görmez (zamanlayıcı her gün yalnızca o
güne kadarki veriyle çalışmış gibi aynı sonucu verir). Aynı durum sürdükçe her gün değil, durumun başladığı gün
bir kez uyarılır: durum bir süre (BIRLESTIRME) kesilip yeniden başlarsa yeni uyarı olur.

Altı kural:
- nakit_esigi (kural): ana TL hesabının gün içi en düşük bakiyesi güvenlik eşiğinin (son 12 ayın ortalama
  aylık satışının %10'u) altına indi.
- buyuk_cikis (kural + istatistik): faturasız, daha önce ödeme yapılmamış bir karşı tarafa, son bir yılın
  tedarikçi ödemelerinin %95'inden büyük bir çıkış.
- mukerrer_odeme (kural): bir tedarikçi faturasına tutarından fazla ödendi.
- karsiliksiz_cek (kural + istatistik): bir müşteri çeki karşılıksız çıktı. Tutarı son bir yılın çeklerinin üst
  çeyreğindeyse "uyari", değilse "bilgi".
- eslesmeyen_birikim (istatistik): son 14 günde faturası belli olmayan tahsilat sayısı, geçmiş bir yılın
  ortalamasına göre şansla açıklanamayacak kadar yüksek (Poisson, p < 0,001).
- musteri_gecikmesi (istatistik): son 12 ayın en büyük 20 müşterisinden birinin havale ile ödenen faturalarından,
  müşterinin kendi ödeme alışkanlığına göre şansla açıklanamayacak kadar çoğu açık kaldı (Poisson-binom, p < 0,01).

Eşikler Ayarlar'dadır; her deneme reports/faz4_ayar_denemeleri.json'a yazılır.
"""

from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import heapq
import math
from collections import defaultdict
from dataclasses import asdict, dataclass

from sayman.data.as_of import kapanis_tarihleri

GUN = dt.timedelta(days=1)
AY_GUN = 30.4375


@dataclass(frozen=True)
class Ayarlar:
    isinma_gun: int = 30  # verinin ilk bu kadar gününde uyarı verilmez: karşılaştıracak geçmiş yok
    gecmis_gun: int = 365  # istatistiklerin baktığı geçmiş
    # nakit_esigi
    nakit_hesabi: str = "A_TRY"
    asgari_nakit_orani: float = 0.10  # son 12 ayın ortalama aylık net satışının bu kadarı (güvenlik eşiği)
    nakit_birlestirme: int = 7
    # buyuk_cikis
    cikis_yuzdeligi: float = 0.95
    cikis_en_az_gecmis: int = 20
    # karsiliksiz_cek
    cek_yuzdeligi: float = 0.75
    cek_en_az_gecmis: int = 20
    # eslesmeyen_birikim
    birikim_pencere: int = 14
    birikim_p: float = 0.001
    birikim_en_az: int = 5
    birikim_en_az_gecmis: int = 60
    birikim_birlestirme: int = 14
    # musteri_gecikmesi
    buyuk_musteri_sayisi: int = 20
    gecikme_pencere: int = 45  # vadesi son bu kadar günde dolan faturalar test edilir
    gecikme_p: float = 0.01
    gecikme_fatura_sayisi: int = 2
    gecikme_en_az_gecmis: int = 10  # tabanda en az bu kadar havale ile ödenen fatura
    gecikme_birlestirme: int = 30

    def sozluk(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- yardımcılar


def tl_metin(kurus: int) -> str:
    k = abs(int(kurus))
    tam = f"{k // 100:,}".replace(",", ".")
    return f"{'-' if kurus < 0 else ''}{tam},{k % 100:02d} TL"


def tl(kurus: int) -> str:
    k = int(kurus)
    return f"{'-' if k < 0 else ''}{abs(k) // 100}.{abs(k) % 100:02d}"


def sayi(x: float, basamak: int) -> str:
    """Ondalık virgüllü sayı: 0.00047 -> "0,00047"."""
    return f"{x:.{basamak}f}".replace(".", ",")


def yuzdelik(degerler: list[int], oran: float) -> int:
    s = sorted(degerler)
    return s[min(int(len(s) * oran), len(s) - 1)]


def poisson_kuyruk(n: int, lam: float) -> float:
    """P(X >= n), X ~ Poisson(lam)."""
    if n <= 0:
        return 1.0
    terim, toplam = math.exp(-lam), 0.0
    for k in range(n):
        toplam += terim
        terim *= lam / (k + 1)
    return max(0.0, 1.0 - toplam)


def poisson_binom_kuyruk(k: int, olasiliklar: list[float]) -> float:
    """P(X >= k), X: her biri kendi olasılığıyla gerçekleşen bağımsız olayların sayısı."""
    dagilim = [1.0] + [0.0] * len(olasiliklar)
    for p in olasiliklar:
        for j in range(len(dagilim) - 1, 0, -1):
            dagilim[j] = dagilim[j] * (1 - p) + dagilim[j - 1] * p
        dagilim[0] *= 1 - p
    return min(1.0, sum(dagilim[k:]))


def gunler(bas: dt.date, bit: dt.date):
    g = bas
    while g <= bit:
        yield g
        g += GUN


def uyari(kural: str, gun: dt.date, onem: str, varlik_tur: str, varlik_id: str, baslik: str, aciklama: str,
          olcum: dict, esik: dict, kaynak: dict, musteri_id: str | None = None) -> dict:
    kimlik = hashlib.sha256(f"{kural}|{varlik_id}|{gun.isoformat()}".encode("utf-8")).hexdigest()[:12]
    return {"uyari_id": f"U{kimlik}", "kural": kural, "gun": gun.isoformat(), "onem": onem,
            "varlik": {"tur": varlik_tur, "id": varlik_id}, "musteri_id": musteri_id, "baslik": baslik,
            "aciklama": aciklama, "olcum": olcum, "esik": esik, "kaynak": {k: sorted(v) for k, v in kaynak.items()}}


def baslangiclar(kosul_gunleri, birlestirme: int) -> list[dt.date]:
    """Koşulun doğru olduğu günlerden, durumun başladığı günler: önceki `birlestirme` gün içinde koşul hiç
    doğru değilse o gün yeni bir başlangıçtır. Yalnızca geçmiş günlere bakar."""
    sonuc, onceki = [], None
    for g in sorted(kosul_gunleri):
        if onceki is None or (g - onceki).days > birlestirme:
            sonuc.append(g)
        onceki = g
    return sonuc


# ---------------------------------------------------------------- veri


class Veri:
    """Bir güne (bitis) kadar görünür kayıtlar ve kuralların ortak dizinleri."""

    def __init__(self, tablolar: dict[str, list[dict]], baslangic: dt.date, bitis: dt.date):
        self.t, self.baslangic, self.bitis = tablolar, baslangic, bitis
        self.musteri_ad = {m["musteri_id"]: m["ad"] for m in tablolar["musteriler"]}
        self.tedarikci_ad = {t["tedarikci_id"]: t["ad"] for t in tablolar["tedarikciler"]}
        self.gunluk_satis: dict[dt.date, int] = defaultdict(int)  # TL, net (iadeler eksi), POS dahil
        self.musteri_gunluk_satis: dict[dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for f in tablolar["faturalar"]:
            self.gunluk_satis[f["fatura_tarihi"]] += f["tutar_try_kurus"]
            self.musteri_gunluk_satis[f["fatura_tarihi"]][f["musteri_id"]] += f["tutar_try_kurus"]
        for p in tablolar["pos_islemleri"]:
            self.gunluk_satis[p["islem_tarihi"]] += p["tutar_kurus"]

    def donem(self, g: dt.date, gecmis_gun: int) -> str:
        """Kuralın baktığı geçmiş: tam bir yıl varsa "son 12 ayın", yoksa elde olan kadarı."""
        gun = (g - self.baslangic).days
        return "son 12 ayın" if gun >= gecmis_gun else f"son {max(1, round(gun / AY_GUN))} ayın"


# ---------------------------------------------------------------- kurallar


def nakit_esigi(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    hesap = next(h for h in v.t["banka_hesaplari"] if h["hesap_id"] == a.nakit_hesabi)
    gunluk = defaultdict(list)
    for h in v.t["banka_hareketleri"]:
        if h["hesap_id"] == a.nakit_hesabi:
            gunluk[h["tarih"]].append(h)
    kapanis, pencere_toplam = hesap["acilis_bakiye_kurus"], 0
    kosul, olcumler = [], {}
    for g in gunler(v.baslangic, bit):
        pencere_toplam += v.gunluk_satis.get(g, 0)
        eski = g - a.gecmis_gun * GUN
        pencere_toplam -= v.gunluk_satis.get(eski, 0)
        satirlar = gunluk.get(g, [])
        en_dusuk = min([kapanis] + [s["bakiye_kurus"] for s in satirlar])
        kapanis += sum(s["tutar_kurus"] for s in satirlar)
        if g < hesap["acilis_tarihi"] or (g - v.baslangic).days < a.isinma_gun:
            continue
        gun_sayisi = min(a.gecmis_gun, (g - v.baslangic).days + 1)
        aylik = pencere_toplam / gun_sayisi * AY_GUN
        esik = int(aylik * a.asgari_nakit_orani)
        if en_dusuk < esik:
            kosul.append(g)
            olcumler[g] = (en_dusuk, esik, int(aylik), [s["hareket_id"] for s in satirlar])
    sonuc = []
    for g in baslangiclar(kosul, a.nakit_birlestirme):
        if not bas <= g <= bit:
            continue
        en_dusuk, esik, aylik, hareketler = olcumler[g]
        sonuc.append(uyari(
            "nakit_esigi", g, "uyari", "banka_hesabi", a.nakit_hesabi, "Ana hesapta nakit güvenlik eşiğinin altına indi",
            f"{a.nakit_hesabi} hesabının gün içi en düşük bakiyesi {tl_metin(en_dusuk)}; güvenlik eşiği "
            f"{tl_metin(esik)} ({v.donem(g, a.gecmis_gun)} ortalama aylık satışı {tl_metin(aylik)}'nin %{a.asgari_nakit_orani * 100:g}'u).",
            {"en_dusuk_bakiye_tl": tl(en_dusuk)}, {"guvenlik_esigi_tl": tl(esik), "ortalama_aylik_satis_tl": tl(aylik)},
            {"banka_hareketleri": hareketler}))
    return sonuc


def buyuk_cikis(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    odemeler = sorted(v.t["odemeler"], key=lambda o: (o["tarih"], o["odeme_id"]))
    tedarikci = [(o["tarih"], o["tutar_kurus"]) for o in odemeler if o["tur"] == "tedarikci"]
    tedarikci_gunleri = [t for t, _ in tedarikci]
    gorulen: dict[str, dt.date] = {}  # açıklama -> ilk görüldüğü gün
    for o in odemeler:
        gorulen.setdefault(o["aciklama"], o["tarih"])
    sonuc = []
    for o in odemeler:
        g = o["tarih"]
        if o["tur"] != "diger" or not bas <= g <= bit or (g - v.baslangic).days < a.isinma_gun:
            continue
        if gorulen[o["aciklama"]] < g:  # bu karşı tarafa daha önce ödeme yapılmış
            continue
        i, j = bisect.bisect_left(tedarikci_gunleri, g - a.gecmis_gun * GUN), bisect.bisect_left(tedarikci_gunleri, g)
        gecmis = [t for _, t in tedarikci[i:j]]
        if len(gecmis) < a.cikis_en_az_gecmis:
            continue
        esik = yuzdelik(gecmis, a.cikis_yuzdeligi)
        if o["tutar_kurus"] < esik:
            continue
        sonuc.append(uyari(
            "buyuk_cikis", g, "uyari", "odeme", o["odeme_id"], "Yeni bir karşı tarafa olağandışı büyük ödeme",
            f"{tl_metin(o['tutar_kurus'])} tutarında, bir faturaya bağlı olmayan ödeme ({o['aciklama']}). Bu karşı "
            f"tarafa daha önce ödeme yapılmamış; tutar {v.donem(g, a.gecmis_gun)} tedarikçi ödemelerinin %95'inden "
            f"({tl_metin(esik)}) büyük.",
            {"tutar_tl": tl(o["tutar_kurus"]), "kat": f"{o['tutar_kurus'] / esik:.2f}"},
            {"tedarikci_odemeleri_yuzde_95_tl": tl(esik)}, {"odemeler": [o["odeme_id"]]}))
    return sonuc


def mukerrer_odeme(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    faturalar = {f["alis_fatura_id"]: f for f in v.t["alis_faturalari"]}
    odemeler = defaultdict(list)
    for o in v.t["odemeler"]:
        if o["alis_fatura_id"]:
            odemeler[o["alis_fatura_id"]].append(o)
    sonuc = []
    for fid, liste in sorted(odemeler.items()):
        f = faturalar.get(fid)
        if f is None:
            continue
        toplam, kullanilan = 0, []
        for o in sorted(liste, key=lambda o: (o["tarih"], o["odeme_id"])):
            toplam += o["tutar_kurus"]
            kullanilan.append(o["odeme_id"])
            if toplam > f["tutar_kurus"]:
                g = max(o["tarih"], f["tarih"])  # fatura ödemeden sonra görünürse o gün anlaşılır
                if bas <= g <= bit:
                    sonuc.append(uyari(
                        "mukerrer_odeme", g, "uyari", "alis_faturasi", fid, "Tedarikçi faturasına fazla ödeme",
                        f"{v.tedarikci_ad.get(f['tedarikci_id'], f['tedarikci_id'])} tedarikçisinin {fid} numaralı "
                        f"{tl_metin(f['tutar_kurus'])} tutarındaki faturasına toplam "
                        f"{tl_metin(toplam)} ödendi ({len(kullanilan)} ödeme); fazla ödenen {tl_metin(toplam - f['tutar_kurus'])}.",
                        {"odenen_tl": tl(toplam), "fazla_tl": tl(toplam - f["tutar_kurus"])},
                        {"fatura_tutari_tl": tl(f["tutar_kurus"])},
                        {"alis_faturalari": [fid], "odemeler": kullanilan}))
                break
    return sonuc


def karsiliksiz_cek(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    cekler = {c["cek_id"]: c for c in v.t["cekler"]}
    alislar = sorted((c["alis_tarihi"], c["tutar_kurus"]) for c in v.t["cekler"])
    alis_gunleri = [t for t, _ in alislar]
    sonuc = []
    for h in sorted(v.t["cek_hareketleri"], key=lambda h: (h["tarih"], h["cek_hareket_id"])):
        g = h["tarih"]
        if h["durum"] != "karsiliksiz" or not bas <= g <= bit:
            continue
        c = cekler[h["cek_id"]]
        i, j = bisect.bisect_left(alis_gunleri, g - (a.gecmis_gun - 1) * GUN), bisect.bisect_right(alis_gunleri, g)
        gecmis = [t for _, t in alislar[i:j]]
        esik = yuzdelik(gecmis, a.cek_yuzdeligi) if len(gecmis) >= a.cek_en_az_gecmis else None
        onem = "uyari" if esik is None or c["tutar_kurus"] >= esik else "bilgi"
        esik_metni = f"{v.donem(g, a.gecmis_gun)} çeklerinin üst çeyreği {tl_metin(esik)}'den başlıyor" if esik else \
            "karşılaştıracak yeterli çek yok"
        sonuc.append(uyari(
            "karsiliksiz_cek", g, onem, "cek", c["cek_id"], "Müşteri çeki karşılıksız çıktı",
            f"{v.musteri_ad.get(c['musteri_id'], c['musteri_id'])} müşterisinin {c['kesideci_banka']} çeki ({tl_metin(c['tutar_kurus'])}, vade "
            f"{c['vade_tarihi'].isoformat()}) karşılıksız çıktı; {esik_metni}.",
            {"tutar_tl": tl(c["tutar_kurus"])}, {"ust_ceyrek_tl": tl(esik) if esik else None},
            {"cekler": [c["cek_id"]], "cek_hareketleri": [h["cek_hareket_id"]], "musteriler": [c["musteri_id"]]},
            musteri_id=c["musteri_id"]))
    return sonuc


def eslesmeyen_birikim(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    eslesmeyen = sorted((t["tarih"], t["tahsilat_id"]) for t in v.t["tahsilatlar"] if t["fatura_id"] is None)
    tarihler = [t for t, _ in eslesmeyen]
    kosul, olcumler = [], {}
    for g in gunler(v.baslangic + a.birikim_en_az_gecmis * GUN, bit):
        pbas = g - (a.birikim_pencere - 1) * GUN
        i, j = bisect.bisect_left(tarihler, pbas), bisect.bisect_right(tarihler, g)
        n = j - i
        if n < a.birikim_en_az:
            continue
        gbas = max(v.baslangic, g - a.gecmis_gun * GUN)
        k = bisect.bisect_left(tarihler, gbas)
        gecmis_gun = (pbas - gbas).days
        lam = (i - k) / gecmis_gun * a.birikim_pencere if gecmis_gun > 0 else 0.0
        p = poisson_kuyruk(n, lam)
        if p < a.birikim_p:
            kosul.append(g)
            olcumler[g] = (n, lam, p, [tid for _, tid in eslesmeyen[i:j]])
    sonuc = []
    for g in baslangiclar(kosul, a.birikim_birlestirme):
        if not bas <= g <= bit:
            continue
        n, lam, p, ids = olcumler[g]
        sonuc.append(uyari(
            "eslesmeyen_birikim", g, "uyari", "sirket", "tahsilatlar", "Faturası belli olmayan tahsilatlar birikti",
            f"Son {a.birikim_pencere} günde faturası belli olmayan {n} tahsilat geldi; {v.donem(g, a.gecmis_gun)} "
            f"ortalamasına göre beklenen {sayi(lam, 1)}. Bu kadarının şansla gelme olasılığı {sayi(p, 5)}.",
            {"tahsilat_sayisi": n}, {"beklenen": f"{lam:.2f}", "olasilik": f"{p:.6f}", "p_esigi": str(a.birikim_p)},
            {"tahsilatlar": ids}))
    return sonuc


def musteri_gecikmesi(v: Veri, a: Ayarlar, bas: dt.date, bit: dt.date) -> list[dict]:
    """Büyük bir müşterinin havale ile ödenen faturaları, o müşterinin kendi alışkanlığına göre şansla
    açıklanamayacak kadar çok açık kaldı mı?

    Çekle ödenen faturalar ayrı tutulur: çekin vadesi baştan bellidir, gecikme havale ile ödenen faturalarda görülür.
    Bir gün g için:
    - Taban: vadesi 12 ay önce ile `gecikme_pencere` gün önce arasında dolan faturalar. Her biri için g'de belli olan
      gecikme (kapanış - vade; kapanmadıysa sonsuz).
    - Test: vadesi son `gecikme_pencere` günde dolan faturalar. Her biri, normalde bu yaşta hâlâ açık olma
      olasılığıyla (tabanda o kadar günden fazla geciken payı, Laplace düzeltmeli) karşılaştırılır.
    - Gözlenen açık sayısının bu olasılıklarla bu kadar yüksek çıkma olasılığı (Poisson-binom kuyruğu) eşikten küçükse
      koşul doğrudur.
    Çekle ödenmiş (kapanışı çek tahsilatıyla olmuş) faturalar ve portföyde aynı tutarda bir çekle karşılanan açık
    faturalar ikisinden de çıkarılır.
    """
    # Her satış faturasının kapandığı gün; bit'e kadar kapanmadıysa None. Kapanış günü yalnızca o güne kadarki
    # kayıtlardan belli olur, sonrasını kullanmaz.
    kapanis = kapanis_tarihleri(v.t, bit)
    cekle_odenen: dict[str, dt.date] = {}
    for t in v.t["tahsilatlar"]:
        if t["cek_id"] and t["fatura_id"]:
            cekle_odenen[t["fatura_id"]] = min(t["tarih"], cekle_odenen.get(t["fatura_id"], t["tarih"]))
    cek_bitis: dict[str, dt.date] = {}
    for h in v.t["cek_hareketleri"]:
        if h["durum"] in ("tahsil_edildi", "karsiliksiz"):
            cek_bitis[h["cek_id"]] = min(h["tarih"], cek_bitis.get(h["cek_id"], h["tarih"]))
    musteri_cekleri = defaultdict(list)  # müşteri -> (alış, tahsil/karşılıksız günü, tutar)
    for c in v.t["cekler"]:
        musteri_cekleri[c["musteri_id"]].append((c["alis_tarihi"], cek_bitis.get(c["cek_id"]), c["tutar_kurus"]))
    musteri_faturalari = defaultdict(list)  # müşteri -> vadeye göre sıralı (vade, fatura, kesim günü, tutar)
    for f in v.t["faturalar"]:
        if f["tur"] == "satis" and f["tutar_kurus"] > 0:
            musteri_faturalari[f["musteri_id"]].append(
                (f["vade_tarihi"], f["fatura_id"], f["fatura_tarihi"], f["tutar_kurus"]))
    for m in musteri_faturalari:
        musteri_faturalari[m].sort()
    vadeler = {m: [x[0] for x in liste] for m, liste in musteri_faturalari.items()}

    pencere: dict[str, int] = defaultdict(int)
    kosul, olcumler = defaultdict(list), {}
    for g in gunler(v.baslangic, bit):
        for m, tutar in v.musteri_gunluk_satis.get(g, {}).items():
            pencere[m] += tutar
        for m, tutar in v.musteri_gunluk_satis.get(g - a.gecmis_gun * GUN, {}).items():
            pencere[m] -= tutar
        if (g - v.baslangic).days < a.isinma_gun:
            continue
        for _, m in heapq.nlargest(a.buyuk_musteri_sayisi, ((t, m) for m, t in pencere.items() if t > 0)):
            liste, vd = musteri_faturalari.get(m, []), vadeler.get(m, [])
            portfoy: dict[int, int] = defaultdict(int)  # g günü portföyde/tahsilde olan çeklerin tutarları
            for alis, son, tutar in musteri_cekleri.get(m, []):
                if alis <= g and (son is None or son > g):
                    portfoy[tutar] += 1

            def havale(fid: str, tutar: int) -> bool:
                k = kapanis[fid]
                if k is not None and k <= g:
                    return not (fid in cekle_odenen and cekle_odenen[fid] <= g)
                return portfoy[tutar] == 0

            i0 = bisect.bisect_left(vd, g - a.gecmis_gun * GUN)
            i1 = bisect.bisect_left(vd, g - a.gecikme_pencere * GUN)
            i2 = bisect.bisect_left(vd, g)
            taban = sorted((kapanis[fid] - vade).days if kapanis[fid] is not None and kapanis[fid] <= g else 10**6
                           for vade, fid, _, tutar in liste[i0:i1] if havale(fid, tutar))
            if len(taban) < a.gecikme_en_az_gecmis:
                continue
            olasiliklar, acik = [], []
            for vade, fid, kesim, tutar in liste[i1:i2]:
                if kesim > g or not havale(fid, tutar):
                    continue
                yas = (g - vade).days
                olasiliklar.append((len(taban) - bisect.bisect_right(taban, yas) + 1) / (len(taban) + 2))
                if kapanis[fid] is None or kapanis[fid] > g:
                    acik.append(fid)
            if len(acik) < a.gecikme_fatura_sayisi:
                continue
            p = poisson_binom_kuyruk(len(acik), olasiliklar)
            if p < a.gecikme_p:
                kosul[m].append(g)
                olcumler[(m, g)] = (sorted(acik), len(olasiliklar), sum(olasiliklar), p, len(taban))
    sonuc = []
    for m, gunler_ in sorted(kosul.items()):
        for g in baslangiclar(gunler_, a.gecikme_birlestirme):
            if not bas <= g <= bit:
                continue
            acik, n, beklenen, p, taban = olcumler[(m, g)]
            sonuc.append(uyari(
                "musteri_gecikmesi", g, "uyari", "musteri", m, "Büyük bir müşteri alışılmışın dışında gecikiyor",
                f"{v.musteri_ad.get(m, m)} {v.donem(g, a.gecmis_gun)} en büyük {a.buyuk_musteri_sayisi} "
                f"müşterisinden biri. Vadesi son "
                f"{a.gecikme_pencere} günde dolan ve çekle karşılanmamış {n} faturasından {len(acik)} tanesi hâlâ "
                f"açık. Müşterinin havale ile ödediği önceki {taban} faturasındaki alışkanlığına göre beklenen "
                f"{sayi(beklenen, 1)}; bu kadarının şansla açık kalma olasılığı {sayi(p, 4)}.",
                {"acik_fatura": len(acik), "incelenen_fatura": n},
                {"beklenen_acik": f"{beklenen:.2f}", "olasilik": f"{p:.6f}", "p_esigi": str(a.gecikme_p),
                 "taban_fatura": taban},
                {"faturalar": acik, "musteriler": [m]}, musteri_id=m))
    return sonuc


KURALLAR = {
    "nakit_esigi": nakit_esigi,
    "buyuk_cikis": buyuk_cikis,
    "mukerrer_odeme": mukerrer_odeme,
    "karsiliksiz_cek": karsiliksiz_cek,
    "eslesmeyen_birikim": eslesmeyen_birikim,
    "musteri_gecikmesi": musteri_gecikmesi,
}
