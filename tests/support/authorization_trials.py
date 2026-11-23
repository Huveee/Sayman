"""60 yetkisiz deneme ve onları değerlendiren hakem (Faz 7'de gerçek modelle yeniden kullanılır).

Deneme tablosu veriden kurulur: başka müdürün müşterileri ve faturaları o günkü veriden seçilir. Her deneme
şunlardan birini bekler:
- ("ret", {kodlar}): çağrı reddedilir; cevapta yalnızca cagri_id, hata ve mesaj vardır.
- ("kapsamli",): çağrıya izin vardır ama kapsam dışı bir şey sorulur (başka müdürün müşterisi, onun faturası).
  Cevap, yalnızca kapsamdaki satırlarla Python'da ayrıca kurulmuş bir ambarda aynı aracın verdiği cevapla
  birebir aynı olmalıdır (kapsam.py'nin SQL süzgecinden bağımsız) ve kapsam dışı hiçbir metin değeri (kimlik,
  ad, fatura no...) içermemelidir.

Gruplar: A yasak araç (12), B sahte ya da bozuk kimlik (12), C başka müdürün verisi (24), D gelecek tarih (6),
E tuhaf girdiler (6). Denemeler gerçek bir MCP istemci oturumundan gönderilir.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from sayman.warehouse.loader import ambar_kur
from sayman.tools import identity
from sayman.tools.catalog import ARACLAR_ADA_GORE, AracHatasi, Baglam
from sayman.data.table_io import csv_yaz

KULLANICILAR = {"cfo": "selin.aydin", "yurtici": "murat.kaya", "ihracat": "deniz.arslan"}
YASAK_ARACLAR = ("nakit_pozisyonu", "banka_hareketleri", "yaklasan_odemeler", "nakit_akis_tahmini", "kur_riski",
                 "pos_tahsilatlari")
# Kimlik saldırısı -> denetim kaydına yazılması beklenen neden. rol_iddiasi: kart geçerli ve doğru imzalı ama
# yükte "rol": "cfo" yazıyor; rol karttan okunmadığı için satış müdürü olarak kalır ve yetki_yok alır.
KIMLIK_SALDIRILARI = {
    "yok": "kart_yok", "bos": "kart_bicimi", "bozuk": "kart_bicimi", "yanlis_anahtar": "imza_gecersiz",
    "degistirilmis": "imza_gecersiz", "imzasiz": "imza_gecersiz", "suresi_dolmus": "kart_suresi_dolmus",
    "bilinmeyen_kullanici": "kullanici_bilinmiyor", "cok_uzun_sure": "kart_suresi_cok_uzun",
    "metin_degil": "kart_bicimi", "kesik": "imza_gecersiz", "rol_iddiasi": "yetki_yok",
}
KAPSAMLAR = ("yurtici", "ihracat")
MUSTERI_TABLOLARI = ("faturalar", "tahsilatlar", "cekler")
YALIN_TARIH = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------- deneme tablosu


def _gecerli_args(arac: str, simdi: dt.date) -> dict:
    once = (simdi - dt.timedelta(days=30)).isoformat()
    return {"nakit_pozisyonu": {}, "banka_hareketleri": {"baslangic": once, "bitis": simdi.isoformat()},
            "yaklasan_odemeler": {"gun_sayisi": 30}, "nakit_akis_tahmini": {"hafta_sayisi": 4}, "kur_riski": {},
            "pos_tahsilatlari": {"baslangic": once, "bitis": simdi.isoformat()}}[arac]


def kapsam_disi(T: dict, kapsam: str, simdi: dt.date) -> dict:
    """Bir satış müdürünün görmemesi gereken, `simdi`ye kadar görünür müşteriler ve faturalar."""
    yurtici = kapsam == "yurtici"
    musteriler = sorted((m for m in T["musteriler"] if m["yurtici"] != yurtici and m["ilk_islem_tarihi"] <= simdi),
                        key=lambda m: m["musteri_id"])
    ids = {m["musteri_id"] for m in musteriler}
    faturalar = sorted((f for f in T["faturalar"] if f["musteri_id"] in ids and f["fatura_tarihi"] <= simdi),
                       key=lambda f: f["fatura_id"])
    return {"musteriler": musteriler, "faturalar": faturalar}


def _kismi_ad(ad: str) -> str:
    kelimeler = ad.split()
    return " ".join(kelimeler[:2]) if len(kelimeler) > 2 else kelimeler[0] if len(kelimeler[0]) >= 3 else ad


def denemeler(T: dict, simdi: dt.date, veri_bitis: dt.date) -> list[dict]:
    if veri_bitis <= simdi:
        raise ValueError("gelecek tarih denemeleri için veri bitişi şimdiden sonra olmalı")
    liste = []

    def ekle(grup, kullanici, arac, argumanlar, beklenen, kart="gecerli"):
        liste.append({"no": len(liste) + 1, "grup": grup, "kullanici": kullanici, "kart": kart, "arac": arac,
                      "argumanlar": argumanlar, "beklenen": beklenen})

    # A: satış müdürleri yasak araçları çağırır (6 x 2)
    for kapsam in KAPSAMLAR:
        for arac in YASAK_ARACLAR:
            ekle("A", KULLANICILAR[kapsam], arac, _gecerli_args(arac, simdi), ("ret", {"yetki_yok"}))

    # B: kimlik saldırıları; hepsi CFO verisini (nakit) ister
    for saldiri, neden in KIMLIK_SALDIRILARI.items():
        kullanici = KULLANICILAR["yurtici"] if saldiri in ("degistirilmis", "rol_iddiasi") else KULLANICILAR["cfo"]
        kod = "yetki_yok" if neden == "yetki_yok" else "kimlik_gecersiz"
        ekle("B", kullanici, "nakit_pozisyonu", {}, ("ret", {kod}), kart=saldiri)

    # C: başka müdürün müşterisi ve faturaları (12 x 2)
    for kapsam in KAPSAMLAR:
        disari = kapsam_disi(T, kapsam, simdi)
        m, f = disari["musteriler"], disari["faturalar"]
        if not m or not f:
            raise ValueError(f"{kapsam} için kapsam dışı müşteri ya da fatura yok; denemeler kurulamaz")
        adlar = [m[i * len(m) // 3]["ad"] for i in range(3)]
        for arac, args in [
            ("musteri_ozeti", {"musteri_adi": adlar[0]}),
            ("musteri_ozeti", {"musteri_adi": _kismi_ad(adlar[1])}),
            ("musteri_ozeti", {"musteri_adi": m[-1]["musteri_id"]}),
            ("alacak_yaslandirma", {"musteri_adi": adlar[2]}),
            ("alacak_yaslandirma", {"musteri_adi": m[0]["musteri_id"].lower()}),
            ("fatura_ara", {"fatura_no": f[0]["fatura_id"]}),
            ("fatura_ara", {"fatura_no": f[-1]["fatura_id"].lower()}),
            ("fatura_ara", {"fatura_no": f[len(f) // 2]["kaynak_fatura_no"]}),
            ("fatura_ara", {"musteri_adi": adlar[0], "durum": "hepsi", "en_fazla": 10}),
            ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}),
            ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
            ("dso_hesapla", {"donem": "son_12_ay"}),
        ]:
            ekle("C", KULLANICILAR[kapsam], arac, args, ("kapsamli",))

    # D: gelecek tarih (aralığın sonu dahil)
    def gun(n):
        return (simdi + dt.timedelta(days=n)).isoformat()

    for kullanici, arac, args in [
        (KULLANICILAR["cfo"], "nakit_pozisyonu", {"tarih": gun(1)}),
        (KULLANICILAR["cfo"], "banka_hareketleri", {"baslangic": simdi.isoformat(), "bitis": gun(30)}),
        (KULLANICILAR["yurtici"], "alacak_yaslandirma", {"tarih": gun(7)}),
        (KULLANICILAR["ihracat"], "fatura_ara", {"tarih": veri_bitis.isoformat()}),
        (KULLANICILAR["cfo"], "musteri_ozeti", {"musteri_adi": "abc", "tarih": gun(1)}),
        (KULLANICILAR["yurtici"], "doviz_kuru", {"para_birimi": "USD", "tarih": gun(1)}),
    ]:
        ekle("D", kullanici, arac, args, ("ret", {"gelecek_tarih"}))

    # E: tuhaf girdiler
    ekle("E", KULLANICILAR["yurtici"], "musteri_ozeti", {"musteri_adi": "' OR 1=1 --"}, ("kapsamli",))
    ekle("E", KULLANICILAR["ihracat"], "fatura_ara", {"fatura_no": "F1' OR '1'='1"}, ("kapsamli",))
    ekle("E", KULLANICILAR["cfo"], "banka_hareketleri",
         {"baslangic": simdi.isoformat(), "bitis": simdi.isoformat(), "sql": "DROP TABLE faturalar"},
         ("ret", {"gecersiz_arguman"}))
    ekle("E", KULLANICILAR["yurtici"], "alacak_yaslandirma", {"en_fazla": 100000}, ("ret", {"gecersiz_arguman"}))
    ekle("E", KULLANICILAR["ihracat"], "fatura_ara", {"musteri_adi": "%"}, ("ret", {"gecersiz_arguman"}))
    ekle("E", KULLANICILAR["cfo"], "banka_transferi", {"tutar": "1000000.00"}, ("ret", {"bilinmeyen_arac"}))
    assert len(liste) == 60, len(liste)
    return liste


def en_genis_cagrilar(simdi: dt.date, en_cok_faturali: str) -> list[tuple[str, dict]]:
    """Her aracın en uzun cevabını veren çağrılar (en_fazla=10, en geniş aralık); fatura_ara bir de varsayılanla."""
    bas = "2024-01-01"
    return [("nakit_pozisyonu", {}), ("banka_hareketleri", {"baslangic": bas, "bitis": simdi.isoformat(), "en_fazla": 10}),
            ("alacak_yaslandirma", {"en_fazla": 10}), ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}),
            ("musteri_ozeti", {"musteri_adi": en_cok_faturali}), ("dso_hesapla", {"donem": "son_12_ay"}),
            ("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}), ("nakit_akis_tahmini", {"hafta_sayisi": 13}),
            ("kur_riski", {}), ("doviz_kuru", {"para_birimi": "EUR"}),
            ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
            ("pos_tahsilatlari", {"baslangic": bas, "bitis": simdi.isoformat()}),
            ("fatura_ara", {"durum": "hepsi", "en_fazla": 10}), ("fatura_ara", {"durum": "hepsi"})]


# ---------------------------------------------------------------- kimlik kartları


def _imzali(icerik: dict, anahtar: bytes) -> str:
    yuk = identity._b64(json.dumps(icerik, separators=(",", ":")).encode("utf-8"))
    return f"{yuk}.{identity._imza(anahtar, yuk)}"


def kart_hazirla(saldiri: str, kullanici: str, anahtar: bytes, zaman: float):
    """(gönderilsin_mi, kart). 'gecerli' doğru kartı, diğerleri adı geçen saldırıyı üretir."""
    son = int(zaman) + 3600
    if saldiri == "gecerli":
        return True, identity.kart_uret(kullanici, anahtar, 3600, zaman)
    if saldiri == "yok":
        return False, None
    kart = {
        "bos": lambda: "",
        "bozuk": lambda: "bu-bir-kart-degil",
        "yanlis_anahtar": lambda: identity.kart_uret(kullanici, hashlib.sha256(anahtar).digest(), 3600, zaman),
        # kendi kartının yükünü CFO olarak değiştirir, imzayı olduğu gibi bırakır
        "degistirilmis": lambda: _imzali({"v": 1, "k": KULLANICILAR["cfo"], "son": son}, b"x").split(".")[0] + "."
        + identity.kart_uret(kullanici, anahtar, 3600, zaman).split(".")[1],
        "imzasiz": lambda: _imzali({"v": 1, "k": kullanici, "son": son}, anahtar).split(".")[0] + ".",
        "suresi_dolmus": lambda: identity.kart_uret(kullanici, anahtar, 3600, zaman - 7200),
        "bilinmeyen_kullanici": lambda: identity.kart_uret("ali.veli", anahtar, 3600, zaman),
        "cok_uzun_sure": lambda: _imzali({"v": 1, "k": kullanici, "son": int(zaman) + 30 * 86400}, anahtar),
        "metin_degil": lambda: 12345,
        "kesik": lambda: identity.kart_uret(kullanici, anahtar, 3600, zaman)[:-6],
        "rol_iddiasi": lambda: _imzali({"v": 1, "k": kullanici, "son": son, "rol": "cfo"}, anahtar),
    }[saldiri]()
    return True, kart


# ---------------------------------------------------------------- MCP istemcisi


async def _oturumda(sunucu, cagrilar):
    cikti = []
    async with create_connected_server_and_client_session(sunucu.mcp) as oturum:
        for ad, args, gonder, kart in cagrilar:
            meta = {identity.META_ANAHTARI: kart} if gonder else None
            r = await oturum.call_tool(ad, args, meta=meta)
            metin = json.loads(r.content[0].text)
            if metin != r.structuredContent:
                raise AssertionError("metin içerik ile yapısal içerik farklı")
            cikti.append((r.structuredContent, bool(r.isError)))
    return cikti


def mcp_cagir(sunucu, cagrilar) -> list[tuple[dict, bool]]:
    """cagrilar: (araç, argümanlar, kart_gonderilsin_mi, kart). Tek bir MCP istemci oturumundan gönderilir."""
    return anyio.run(_oturumda, sunucu, cagrilar)


# ---------------------------------------------------------------- hakem


def kapsam_tablolari(T: dict, kapsam: str) -> dict:
    """Kapsamdaki satırlar, Python'da süzülür (kapsam.py'deki SQL süzgecinden bağımsız)."""
    yurtici = kapsam == "yurtici"
    musteriler = [m for m in T["musteriler"] if bool(m["yurtici"]) == yurtici]
    ids = {m["musteri_id"] for m in musteriler}
    sonuc = {ad: [] for ad in T}
    sonuc["musteriler"] = musteriler
    for ad in MUSTERI_TABLOLARI:
        sonuc[ad] = [s for s in T[ad] if s["musteri_id"] in ids]
    cekler = {c["cek_id"] for c in sonuc["cekler"]}
    sonuc["cek_hareketleri"] = [h for h in T["cek_hareketleri"] if h["cek_id"] in cekler]
    sonuc["doviz_kurlari"] = list(T["doviz_kurlari"])
    return sonuc


def tablo_metinleri(T: dict) -> set[str]:
    return {v for satirlar in T.values() for s in satirlar for v in s.values() if isinstance(v, str)}


def metin_degerleri(nesne) -> set[str]:
    if isinstance(nesne, str):
        return {nesne}
    if isinstance(nesne, dict):
        return set().union(*(metin_degerleri(v) for v in nesne.values()))
    if isinstance(nesne, list):
        return set().union(*(metin_degerleri(v) for v in nesne))
    return set()


class Hakem:
    """Cevapları değerlendirir: kapsamdaki satırlarla ayrıca kurulmuş referans ambarlar ve yasak değerler."""

    def __init__(self, T: dict, simdi: dt.date, klasor: Path):
        self.simdi = simdi
        self.referans, self.yasak = {}, {}
        hepsi = tablo_metinleri(T)
        for kapsam in KAPSAMLAR:
            tablolar = kapsam_tablolari(T, kapsam)
            hedef = Path(klasor) / f"referans_{kapsam}"
            hedef.mkdir(parents=True, exist_ok=True)
            csv_yaz(tablolar, hedef)
            self.referans[kapsam] = ambar_kur(hedef)
            # Kapsam dışı metinler; kapsamda da geçenler (ör. "EUR", ortak banka adı) çıkarılır. Yalın tarihler de
            # çıkarılır (bazı banka hareketlerinin kaynak_id'si bir gündür): bir gün kimseyi tanıtmaz, tarih
            # üzerinden bir sızıntı olursa referans ambarla karşılaştırma onu zaten yakalar.
            self.yasak[kapsam] = {v for v in hepsi - tablo_metinleri(tablolar) if not YALIN_TARIH.fullmatch(v)}
        self.hepsi = hepsi

    def beklenen(self, kapsam: str, arac: str, args: dict) -> dict:
        """Aynı aracın referans ambardaki cevabı. Araç girdi hatası verirse beklenen de o hatadır."""
        try:
            tarih, sonuc, kaynak = ARACLAR_ADA_GORE[arac].calistir(Baglam(self.referans[kapsam], self.simdi), args)
        except AracHatasi as e:
            return {"hata": "gecersiz_arguman", "mesaj": str(e)}
        return json.loads(json.dumps({"tarih": tarih.isoformat(), "sonuc": sonuc, "kaynak": kaynak},
                                     ensure_ascii=False))

    def degerlendir(self, deneme: dict, kapsam: str | None, yanit: dict, hata: bool,
                    yansima_haric: bool = False) -> list[str]:
        """yansima_haric: kullanıcının kendi yazdığı değerler (ör. durum="portfoyde") cevapta geçerse sızıntı
        sayılmaz. 60 denemede kapalıdır; denemelerin hiçbiri girdisini geri yansıtan bir cevap almaz."""
        sorunlar = []
        if deneme["beklenen"][0] == "ret":
            if not hata or set(yanit) != {"cagri_id", "hata", "mesaj"}:
                sorunlar.append("reddedilmedi ya da ret cevabında fazladan alan var")
            elif yanit["hata"] not in deneme["beklenen"][1]:
                sorunlar.append(f"beklenmeyen ret kodu: {yanit['hata']}")
            yasak = self.hepsi
        else:
            beklenen = self.beklenen(kapsam, deneme["arac"], deneme["argumanlar"])
            alanlar = ("hata", "mesaj") if "hata" in beklenen else ("tarih", "sonuc", "kaynak")
            if hata != ("hata" in beklenen) or {k: yanit.get(k) for k in alanlar} != beklenen:
                sorunlar.append("yalnızca kapsamdaki satırlarla kurulan ambarın cevabından farklı")
            yasak = self.yasak[kapsam]
        sizan = metin_degerleri(yanit) & yasak
        if yansima_haric:
            sizan -= metin_degerleri(deneme["argumanlar"])
        if sizan:
            sorunlar.append(f"kapsam dışı değer: {sorted(sizan)[:5]}")
        return sorunlar

    def kapat(self):
        for con in self.referans.values():
            con.close()


def denemeleri_calistir(sunucu, liste: list[dict], anahtar: bytes, zaman: float, hakem: Hakem) -> list[dict]:
    """Denemeleri MCP üzerinden gönderir; her deneme için cevabı, sorunları ve denetim kaydını kontrol eder."""
    kartlar = [kart_hazirla(d["kart"], d["kullanici"], anahtar, zaman) for d in liste]
    once = len(sunucu.kayit.satirlar)
    cevaplar = mcp_cagir(sunucu, [(d["arac"], d["argumanlar"], g, k) for d, (g, k) in zip(liste, kartlar)])
    kayitlar = sunucu.kayit.satirlar[once:]
    sonuclar = []
    for d, (gonder, kart), (yanit, hata), satir in zip(liste, kartlar, cevaplar, kayitlar):
        kapsam = None
        if d["kart"] == "gecerli" or d["kart"] == "rol_iddiasi":
            kapsam = sunucu.yetki.roller[sunucu.yetki.kullanicilar[d["kullanici"]]].kapsam
        sorunlar = hakem.degerlendir(d, kapsam, yanit, hata)
        if satir.get("cagri_id") != yanit.get("cagri_id"):
            sorunlar.append("denetim kaydındaki çağrı bu çağrı değil")
        beklenen_sonuc = "reddedildi" if hata else "tamam"  # cevabın doğruluğunu hakem ayrıca kontrol etti
        if satir.get("sonuc") != beklenen_sonuc:
            sorunlar.append(f"denetim kaydında sonuç {satir.get('sonuc')}")
        if d["grup"] == "B" and satir.get("neden") != KIMLIK_SALDIRILARI[d["kart"]]:
            sorunlar.append(f"denetim kaydında neden {satir.get('neden')}")
        if isinstance(kart, str) and len(kart) > 8 and kart in json.dumps(satir, ensure_ascii=False):
            sorunlar.append("kimlik kartı denetim kaydına yazılmış")
        sonuclar.append({
            "no": d["no"], "grup": d["grup"], "kullanici": d["kullanici"], "kart": d["kart"], "arac": d["arac"],
            "argumanlar": d["argumanlar"],
            "beklenen": "kapsamli" if d["beklenen"][0] == "kapsamli" else "ret: " + "/".join(sorted(d["beklenen"][1])),
            "cevap": f"ret: {yanit['hata']}" if hata else "tamam", "sorunlar": sorunlar})
    if len(kayitlar) != len(liste):
        raise AssertionError(f"{len(liste)} çağrı için {len(kayitlar)} denetim satırı")

    # C grubu boş bir deneme olmasın: aynı soru CFO'ya sorulunca cevap farklıysa müdürden gerçekten bir şey
    # saklanmıştır ("gizledi"). Aynıysa o deneme saklanacak bir veriye dokunmamıştır.
    c_grubu = [(d, yanit) for d, (yanit, _) in zip(liste, cevaplar) if d["grup"] == "C"]
    cfo_kart = identity.kart_uret(KULLANICILAR["cfo"], anahtar, 3600, zaman)
    cfo = mcp_cagir(sunucu, [(d["arac"], d["argumanlar"], True, cfo_kart) for d, _ in c_grubu])

    def govde(y):
        return {k: v for k, v in y.items() if k not in ("cagri_id", "kapsam")}

    gizledi = {d["no"]: govde(y) != govde(c) for (d, y), (c, _) in zip(c_grubu, cfo)}
    for r in sonuclar:
        if r["no"] in gizledi:
            r["gizledi"] = gizledi[r["no"]]
    return sonuclar
