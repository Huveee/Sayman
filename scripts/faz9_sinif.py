"""Faz 9: Faz 8b "yeni" setinin 86 araç sorusunu otomatik araçlara (sahte API) göre sınıflandırır ve dondurur.

Ölçüt "cevabın değeri ulaşılabilir bir API cevabında aynen geçiyor mu?"dur, çağrı sayısı değil: sistem istemi modelin
kendi toplama ya da oran hesaplamasını yasaklar, doğrulayıcı da araç sonucunda olmayan her sayıyı (doğru olsa bile)
durdurur. Sınıflar:
- tek_cagri: tek bir çağrının sonucunda değer aynen var (argüman bir tarih hesabı isteyebilir: "en az 120 gün geçmiş"
  -> vade_bitis = bugün - 120).
- zincir: değer aynen var ama birden çok çağrı gerekir (ad -> kimlik -> kart) ya da iki sonucun karşılaştırılması
  (portföydeki ve tahsildeki çeklerin ilk vadelisi).
- toplama: değer hiçbir cevapta yok; kayıtları toplamak, ortalamak, kurla çevirmek ya da bütün sayfaları taramak
  gerekir (tutara göre sıralama yok).
- yok: gereken tanım ya da yöntem API'de yok (nakit akışı tahmini, döviz pozisyonu) ya da API'nin değeri soru setinin
  tanımından farklı.
- engelli: değer aynen var, plan doğru çalışıyor, ama kâhinin cevabı gerçek doğrulayıcıdan geçmiyor (ör. doğrulayıcının
  müşteri sahipliği kuralı elle araçların alan adlarına bağlı). Alan adları bu yüzden değiştirilmez; sonuç raporlanır.

Her doğrudan (tek_cagri, zincir) soru için plan gerçek kapıdan (OtomatikSunucu), sorunun kullanıcısıyla çalıştırılır;
API'nin verdiği değer soru setindeki beklenen değerle karşılaştırılır. Sonra otomatik kâhin (sayman.deney.kahin)
gerçek orkestratör ve doğrulayıcıyla sorulur; puanlayıcı cevabı doğru saymazsa soru "engelli" olur.

Çıktı: reports/faz9_siniflar.json (özetleriyle: soru seti, API tanımı, planlar). Ölçümden önce bir kez üretilir.

    .venv\\Scripts\\python -I -u scripts\\faz9_sinif.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.araclar.kayit import DenetimKaydi  # noqa: E402
from sayman.araclar.kimlik import kart_uret  # noqa: E402
from sayman.araclar.yetki import yetki_oku  # noqa: E402
from sayman.asistan.orkestrator import Asistan, Ayar  # noqa: E402
from sayman.deney.kahin import DOGRUDAN, OtomatikKahin, cozumle  # noqa: E402
from sayman.deney.otomatik import OtomatikSunucu  # noqa: E402
from sayman.olcum.puanlama import alternatif_geciyor_mu, esdeger_kimlikler, kaynak_fatura_nolari, yol_al  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

SORU_SETI = KOK / "reports" / "faz8b_soru_seti.json"
CIKTI = KOK / "reports" / "faz9_siniflar.json"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
# Otomatik araç listesi Ollama'da 2209 token ölçüldü (reports/faz9_boyut.json); elle araçlarda ölçülen 2336'ya
# varsayılan 2400 ile verilen pay kadar eklendi. Yalnızca ilk model çağrısının tahmininde kullanılır.
OTOMATIK_LISTE_TOKEN = 2300

F, FTEK, T, C = ("faturalar_api_v1_faturalar_get", "fatura_api_v1_faturalar", "tahsilatlar_api_v1_tahsilatlar_get",
                 "cekler_api_v1_cekler_get")
MLISTE, MKART, KUR = "musteriler_api_v1_musteriler_get", "musteri_api_v1_musteriler", "kurlar_api_v1_kurlar_get"
HESAP, HAREKET = "hesaplar_api_v1_banka_hesaplar_get", "hareketler_api_v1_banka_hesaplar"
BEKLEYEN = ("portfoyde", "tahsilde")

TOPLAMA = {
    "oz_gecikme": "Ortalama gecikme için kapanan her faturanın gecikme gününü bulup ortalamak gerekir.",
    "yas_toplam": "Kapsamdaki bütün açık faturaların kalanlarını TL'ye çevirip toplamak gerekir (binlerce kayıt).",
    "cek_bekleyen": "Sayı iki durumun (portföyde, tahsilde) toplamı; tutar bütün bekleyen çeklerin toplamı.",
    "dso": "DSO alacak ve satış toplamlarından hesaplanır.",
    "dso_tarihli": "DSO alacak ve satış toplamlarından hesaplanır.",
    "gec_en_buyuk": "Tutara göre sıralama yok; en büyüğü bulmak için bütün sayfaları taramak gerekir.",
    "nakit_toplam": "Hesap bakiyelerini kurla TL'ye çevirip toplamak gerekir.",
    "pos_komisyon": "Dönemdeki işlemlerin komisyonlarını toplamak gerekir.",
    "ode_toplam": "Vadesi yaklaşan alış faturalarının kalanlarını toplamak gerekir.",
    "yas_dilim": "Dilimdeki bütün açık faturaların kalanlarını TL'ye çevirip toplamak gerekir.",
}
YOK = {
    "akis_ilk_hafta": "Nakit akışı tahmininin yöntemi (hangi alacağın, çekin, borcun hangi hafta sayılacağı) API'de yok.",
    "kur_poz": "Döviz pozisyonunun tanımı (hangi varlık ve borç kalemlerinin netleşeceği) API'de yok.",
}


def adim(arac: str, **argumanlar) -> dict:
    return {"arac": arac, "argumanlar": argumanlar}


def cvp(anahtar: int, cagri: int, *yol) -> dict:
    return {"anahtar": anahtar, "cagri": cagri, "yol": list(yol)}


def esit(api, beklenen) -> bool:
    try:
        return Decimal(str(api)) == Decimal(str(beklenen))
    except (InvalidOperation, ValueError):
        return str(api) == str(beklenen)


def dilim_sinirlari(ad: str) -> tuple[int, int | None]:
    """"gecikme_61_90" -> (61, 90); "gecikme_180_ustu" -> (181, None)."""
    p = ad.split("_")
    return (int(p[1]) + 1, None) if p[2] == "ustu" else (int(p[1]), int(p[2]))


class Planlayici:
    def __init__(self, sunucu: OtomatikSunucu, anahtar: bytes, bugun: dt.date):
        self.sunucu, self.anahtar, self.bugun = sunucu, anahtar, bugun

    def calistir(self, s: dict, plan: list[dict]) -> list[dict]:
        """Planı sorunun kullanıcısıyla çalıştırır; her adımın "sonuc"u (hata olursa RuntimeError)."""
        kart = kart_uret(s["kullanici"], self.anahtar, 900)
        sonuclar = []
        for a in plan:
            yanit, hata = self.sunucu.kapi(a["arac"], cozumle(a["argumanlar"], sonuclar), kart)
            if hata:
                raise RuntimeError(f"{s['soru_id']}: {a['arac']} -> {yanit}")
            sonuclar.append(yanit)
        return sonuclar

    def gun(self, n: int) -> str:
        return (self.bugun - dt.timedelta(days=n)).isoformat()

    def plan(self, s: dict) -> dict:
        n, alt0 = s["sablon"].removeprefix("y_"), s["alternatifler"][0]
        x = alt0["argumanlar"].get("musteri_adi")
        if n in TOPLAMA:
            return {"sinif": "toplama", "gerekce": TOPLAMA[n]}
        if n in YOK:
            return {"sinif": "yok", "gerekce": YOK[n]}
        if n == "oz_son_tahsilat":
            return {"sinif": "tek_cagri", "plan": [adim(T, musteri_adi=x, sayfa_boyutu=1)],
                    "cevap": [cvp(0, 0, "kayitlar", 0, "tarih"), cvp(1, 0, "kayitlar", 0, "tutar")]}
        if n == "oz_eski_vade":
            return {"sinif": "tek_cagri",
                    "plan": [adim(F, musteri_adi=x, durum="acik", siralama="vade_artan", sayfa_boyutu=1)],
                    "cevap": [cvp(0, 0, "kayitlar", 0, "vade_tarihi")]}
        if n == "oz_acik":
            return {"sinif": "zincir", "gerekce": "Müşteri adından kimliğe, kimlikten müşteri kartına (cari bakiye).",
                    "plan": [adim(MLISTE, ad=x, sayfa_boyutu=1),
                             adim(MKART, musteri_id={"$": [0, "kayitlar", 0, "musteri_id"]})],
                    "cevap": [cvp(0, 1, "cari_bakiye_tl")]}
        if n == "gec_adet":
            return {"sinif": "tek_cagri", "gerekce": "Sayı toplam_kayit alanında; vade_bitis = bugün - gün.",
                    "plan": [adim(F, durum="acik", vade_bitis=self.gun(alt0["argumanlar"]["min_gun"]), sayfa_boyutu=1)],
                    "cevap": [cvp(0, 0, "toplam_kayit")]}
        if n == "fat_son":
            return {"sinif": "tek_cagri", "plan": [adim(F, musteri_adi=x, sayfa_boyutu=1)],
                    "cevap": [cvp(0, 0, "kayitlar", 0, "fatura_id")]}
        if n == "kur_bugun":
            return {"sinif": "tek_cagri", "plan": [adim(KUR, para_birimi=alt0["argumanlar"]["para_birimi"])],
                    "cevap": [cvp(0, 0, "alis")]}
        if n == "fat_tutar":
            return {"sinif": "tek_cagri", "plan": [adim(FTEK, fatura_id=alt0["argumanlar"]["fatura_no"])],
                    "cevap": [cvp(0, 0, "tutar")]}
        if n == "cek_en_erken_no":
            plan = [adim(C, durum="portfoyde", sayfa_boyutu=1), adim(C, durum="tahsilde", sayfa_boyutu=1)]
            ilk = [(r["sonuc"]["kayitlar"][0]["vade_tarihi"], i) for i, r in enumerate(self.calistir(s, plan))
                   if r["sonuc"]["kayitlar"]]
            i = min(ilk)[1]
            return {"sinif": "zincir", "gerekce": "Portföydeki ve tahsildeki çeklerin ilk vadelisi karşılaştırılır.",
                    "plan": plan, "cevap": [cvp(0, i, "kayitlar", 0, "cek_id"), cvp(1, i, "kayitlar", 0, "musteri")]}
        if n == "oz_cek_tutar":
            plan = [adim(C, musteri_adi=x)]
            sonuc = self.calistir(s, plan)[0]["sonuc"]
            bekleyen = [i for i, r in enumerate(sonuc["kayitlar"]) if r["durum"] in BEKLEYEN]
            if sonuc["toplam_kayit"] <= len(sonuc["kayitlar"]):
                if len(bekleyen) == 1:
                    return {"sinif": "tek_cagri", "gerekce": "Müşterinin bekleyen tek bir çeki var.", "plan": plan,
                            "cevap": [cvp(0, 0, "kayitlar", bekleyen[0], "tutar")]}
                return {"sinif": "toplama", "gerekce": f"Bekleyen {len(bekleyen)} çek var; tutarları toplamak gerekir."}
            # Çekler bir sayfaya sığmıyor: bekleyenler durum süzgeciyle iki çağrıda bulunur.
            plan = [adim(C, musteri_adi=x, durum=d) for d in BEKLEYEN]
            sayilar = [r["sonuc"]["toplam_kayit"] for r in self.calistir(s, plan)]
            if sum(sayilar) == 1:
                return {"sinif": "zincir", "gerekce": "Bekleyen tek çek, durum süzgeciyle iki çağrıda bulunur.",
                        "plan": plan, "cevap": [cvp(0, sayilar.index(1), "kayitlar", 0, "tutar")]}
            return {"sinif": "toplama", "gerekce": f"Müşterinin {sonuc['toplam_kayit']} çeki var, {sum(sayilar)} tanesi "
                                                   "bekliyor; tutarları toplamak gerekir."}
        if n == "oz_dilim":
            a, b = dilim_sinirlari(alt0["anahtarlar"][0]["yol"][-1])
            arg = {"musteri_adi": x, "durum": "acik", "vade_bitis": self.gun(a)}
            if b is not None:
                arg["vade_baslangic"] = self.gun(b)
            plan = [adim(F, **arg)]
            sonuc = self.calistir(s, plan)[0]["sonuc"]
            if sonuc["toplam_kayit"] != 1:
                return {"sinif": "toplama", "gerekce": f"Dilimde {sonuc['toplam_kayit']} fatura var; kalanlarını "
                                                       "toplamak gerekir."}
            r = sonuc["kayitlar"][0]
            if r["para_birimi"] == "TRY":
                return {"sinif": "tek_cagri", "gerekce": "Dilimde tek bir TL faturası var (kalan tutar).", "plan": plan,
                        "cevap": [cvp(0, 0, "kayitlar", 0, "kalan_tutar")]}
            if r["kalan_tutar"] == r["tutar"]:
                return {"sinif": "tek_cagri", "gerekce": "Dilimde tek bir döviz faturası var, hiç ödenmemiş (TL karşılığı).",
                        "plan": plan, "cevap": [cvp(0, 0, "kayitlar", 0, "tutar_tl")]}
            return {"sinif": "toplama", "gerekce": "Dilimdeki tek döviz faturası kısmen ödenmiş; kalanın TL karşılığı "
                                                   "için kur çarpımı gerekir."}
        if n == "banka_adet":
            arg = dict(alt0["argumanlar"])
            hesaplar = self.calistir(s, [adim(HESAP)])[0]["sonuc"]["hesaplar"]
            plan = [adim(HESAP)] + [adim(HAREKET, hesap_id={"$": [0, "hesaplar", i, "hesap_id"]}, sayfa_boyutu=1, **arg)
                                    for i in range(len(hesaplar))]
            sayilar = [r["sonuc"]["toplam_kayit"] for r in self.calistir(s, plan)[1:]]
            dolu = [i for i, k in enumerate(sayilar) if k]
            if len(dolu) == 1:
                return {"sinif": "zincir", "gerekce": "Yalnızca bir hesapta hareket var.", "plan": plan,
                        "cevap": [cvp(0, 1 + dolu[0], "toplam_kayit")]}
            return {"sinif": "toplama", "gerekce": f"{len(dolu)} hesapta hareket var ({sayilar}); sayıları toplamak "
                                                   "gerekir."}
        raise ValueError(f"sınıflandırılmamış niyet: {n}")


def ozet(x) -> str:
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    sorular = [s for s in veri["sorular"] if s["set"] == "yeni" and s["bolum"] == "arac"]
    saat = saat_oku(KOK / "data" / "saat.json")
    bugun = saat.simdi
    anahtar = secrets.token_bytes(32)
    kayitlar = []
    with tempfile.TemporaryDirectory() as g:
        sunucu = OtomatikSunucu(AMBAR, lambda: saat, anahtar, yetki_oku(), DenetimKaydi(Path(g) / "d.jsonl"),
                                Path(g) / "kapsam")
        try:
            p = Planlayici(sunucu, anahtar, bugun)
            planlar = {}
            for s in sorular:
                k = {"soru_id": s["soru_id"], "niyet": s["sablon"].removeprefix("y_"), "rol": s["rol"], "alternatif": 0, **p.plan(s)}
                if k["sinif"] in DOGRUDAN:
                    sonuclar = p.calistir(s, k["plan"])
                    # Beklenen değeri veren seçenek (eşit sıralı çekler gibi birden çok doğru cevap olabilir).
                    api = [yol_al(sonuclar[c["cagri"]]["sonuc"], c["yol"]) for c in k["cevap"]]
                    uyan = [i for i, alt in enumerate(s["alternatifler"])
                            if len(alt["anahtarlar"]) >= len(k["cevap"])
                            and all(esit(v, alt["anahtarlar"][c["anahtar"]]["deger"]) for v, c in zip(api, k["cevap"]))]
                    k["api_degerleri"] = api
                    if uyan:
                        k["alternatif"] = uyan[0]
                    else:
                        k.update(sinif="yok", gerekce="API değeri soru setindeki tanımdan farklı: " + ", ".join(
                            f"{v} ≠ {s['alternatifler'][0]['anahtarlar'][c['anahtar']]['deger']}"
                            for v, c in zip(api, k["cevap"])), eski_sinif=k["sinif"])
                planlar[s["soru_id"]] = k
                kayitlar.append(k)

            # Kâhin gerçek orkestratör ve doğrulayıcıyla: puanlayıcı doğru saymazsa "engelli".
            kahin = OtomatikKahin(planlar)
            asistan = Asistan(sunucu, kahin, Ayar(arac_listesi_token=OTOMATIK_LISTE_TOKEN))
            asistan.araclar = sunucu.arac_tanimlari()
            for s in sorular:
                k = planlar[s["soru_id"]]
                if k["sinif"] not in DOGRUDAN:
                    continue
                kahin.soru = s
                tur = asistan.sor(s["soru"], kart_uret(s["kullanici"], anahtar, 900))
                basarili = [{"hata": c["yanit"].get("hata"), "yanit": c["yanit"]} for c in tur.cagrilar
                            if not c["hata_mi"]]
                kabul = esdeger_kimlikler(s["alternatifler"], kaynak_fatura_nolari(basarili))
                dogru = tur.durum == "tamam" and alternatif_geciyor_mu(tur.cevap, kabul, bugun.year)
                k["kahin"] = {"durum": tur.durum, "dogru": dogru, "cevap": tur.cevap,
                              "dogrulama": [{"gecerli": d.gecerli, "kaynaksiz": [x.metin for x in d.kaynaksiz],
                                             "kimliksiz": d.kimliksiz, "sahipsiz": d.sahipsiz} for d in tur.dogrulamalar]}
                if not dogru:
                    k.update(sinif="engelli", eski_sinif=k["sinif"],
                             gerekce=(k.get("gerekce", "") + " Kâhinin cevabı doğrulayıcıdan geçmedi.").strip())
        finally:
            sunucu.kapat()
        api_ozeti = sunucu.ozet()

    sayilar = Counter(k["sinif"] for k in kayitlar)
    sonuc = {
        "aciklama": "Faz 9 sınıflandırması: değer ulaşılabilir bir API cevabında aynen geçiyor mu (scripts/faz9_sinif.py).",
        "bugun": bugun.isoformat(), "soru_seti": SORU_SETI.name, "soru_seti_ozeti": veri["ozet"],
        "api_ozeti": api_ozeti, "otomatik_liste_token": OTOMATIK_LISTE_TOKEN,
        "sayilar": dict(sorted(sayilar.items())),
        "niyetler": {n: dict(Counter(k["sinif"] for k in kayitlar if k["niyet"] == n))
                     for n in sorted({k["niyet"] for k in kayitlar})},
        "sorular": kayitlar,
    }
    sonuc["ozet"] = ozet(sonuc["sorular"])
    CIKTI.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: sonuc[k] for k in ("sayilar", "niyetler", "api_ozeti", "ozet")}, ensure_ascii=False, indent=1))
    for k in kayitlar:
        if k["sinif"] in ("engelli", "yok") and "eski_sinif" in k:
            print(k["soru_id"], k["sinif"], k["gerekce"], json.dumps(k.get("kahin"), ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
