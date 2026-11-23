"""Faz 10 demosu: sahte ERP sayfası, içine gömülü Sayman sohbet penceresi, kimliği imzalayan arka uç ve
"zamanı ileri sar" düğmesi. Çalıştırma ve sıfırlama: scripts/faz10_demo.py.

Güvenlik:
- Tarayıcı imza anahtarını da kimlik kartını da hiç görmez. Girişte arka uç rastgele bir oturum kimliği üretir
  (HttpOnly çerez); her soru ve rapor için kart, oturumdaki kullanıcı adına burada imzalanır. İstek gövdesindeki
  bir kullanıcı alanı yok sayılır. (Demoda parola yok: gerçek bir ERP'de oturumu ERP'nin kendi girişi açar.)
- Araç sonuçları Faz 3 kapısından geçer: rol izni ve veri kapsamı orada uygulanır.
- Uyarılar kapıdan geçmez (izleme bütün şirketin tablolarında çalışır). Bu yüzden burada süzülür: CFO hepsini görür;
  satış müdürü yalnızca kendi kapsamındaki müşterilerin uyarılarını görür, müşterisi olmayan (şirket düzeyindeki)
  uyarıları görmez.
- Rapor, oturumdaki kullanıcının kartıyla kapıdan çağrılan araçlardan kurulur; model rapora karışmaz. Rolün
  çağıramadığı aracın bölümü raporda yer almaz.
- Demo durumu (saat kopyası, uyarı deposu, denetim kayıtları, kapsam kopyaları) kendi klasöründe durur; ambar ve
  data/saat.json değişmez. Saat geri alınamaz; baştan başlamak için klasör silinir.
"""

from __future__ import annotations

import datetime as dt
import secrets
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from sayman.araclar.kayit import DenetimKaydi
from sayman.araclar.kimlik import kart_uret
from sayman.araclar.sunucu import KAPSAM_ADI, SaymanSunucusu
from sayman.araclar.yetki import YetkiTablosu
from sayman.asistan.orkestrator import SISTEM_ISTEMI, Asistan, Ayar, arac_tanimlari
from sayman.izleme.zamanlayici import Depo, calistir
from sayman.veri.saat import Saat, saat_oku, saat_yaz

STATIK = Path(__file__).with_name("static")
CEREZ = "sayman_oturum"
KART_SURESI_S = 600          # bir tur en çok 300 sn sürer (orkestrator.Ayar.sure_s); kart turdan uzun yaşamalı
EN_UZUN_SORU = 500
EN_COK_ADIM_GUN = 31
GOSTERILEN_UYARI = 20
EN_COK_OTURUM = 50
ADLAR = {"selin.aydin": "Selin Aydın", "murat.kaya": "Murat Kaya", "deniz.arslan": "Deniz Arslan"}  # kurgusal
ORNEK_SORULAR = {
    "cfo": ["Bankalarda toplam ne kadar nakit var?",
            "Önümüzdeki 7 günde tedarikçilere ne kadar ödeme yapacağız?",
            "Vadesi geçmiş alacaklarımızın toplamı ne kadar?",
            "Çek kabul politikamıza göre en fazla kaç gün vadeli çek alabiliriz?",
            "Çeki karşılıksız çıkan bir müşteriyle politikamıza göre nasıl çalışmalıyız?"],
    "satis": ["Vadesi geçmiş alacaklarımızın toplamı ne kadar?",
              "Bekleyen çeklerimizin toplamı ne kadar?",
              "Bankalarda toplam ne kadar nakit var?"],
}
# Canlı provada (22-23 Kasım 2026, sayman-ayarli) bu sorular doğrulanmış cevap verdi; satış müdürünün nakit sorusu
# bilerek "yetkiniz yok" ile biter. "Nakit politikamıza göre asgari nakit eşiği" sorusu durduruldu: politika "on iki
# ay" yazıyor, model "12" yazınca doğrulayıcı sayıyı kaynakta bulamadı (bilinen sınır, README Faz 10).
UYARI_ALANLARI = ("uyari_id", "gun", "onem", "baslik", "aciklama", "musteri_id")


def kapsamli_aciklama(kural: str | None, aciklama: str, baslik: str) -> str:
    """Satış müdürüne gösterilecek açıklama: yalnızca o müşterinin kendi kayıtlarından gelen kısım."""
    if kural == "karsiliksiz_cek":       # "... karşılıksız çıktı; <şirketin çek eşiği>."
        return aciklama.split(";")[0].rstrip(".") + "."
    if kural == "musteri_gecikmesi":     # "<müşteri şirketin en büyük N müşterisinden biri>. <kendi faturaları>"
        isaret = "müşterisinden biri. "  # müşteri adında da ". " olabilir (Ltd. Şti.): cümleye değil işarete bakılır
        return aciklama.split(isaret, 1)[1] if isaret in aciklama else baslik + "."
    return baslik + "."                  # bilinmeyen kural: ayrıntı verilmez


class DemoHatasi(RuntimeError):
    """Kullanıcıya gösterilecek, durumu değiştirmeyen bir red (ör. verinin sonuna gelindi)."""


def tl(deger) -> str:
    """Araç biçimi "75826754.65" -> "75.826.754,65 TL"."""
    d = Decimal(str(deger))
    tam, kesir = f"{abs(d):.2f}".split(".")
    return ("-" if d < 0 else "") + f"{int(tam):,}".replace(",", ".") + "," + kesir + " TL"


def _nakit(s):
    return tl(s["toplam_tl"]), [(f"{h['banka']} · {h['para_birimi']}", tl(h["bakiye_tl"])) for h in s["hesaplar"]]


def _odemeler(s):
    return tl(s["toplam_tl"]), [("Fatura sayısı", str(s["fatura_sayisi"])),
                                ("İçinde vadesi geçmiş", tl(s["vadesi_gecmis_tl"]))]


def _alacaklar(s):
    return tl(s["toplam_tl"]), ([("Vadesi geçmiş", tl(s["vadesi_gecmis_tl"])),
                                 ("Müşteri sayısı", str(s["musteri_sayisi"]))]
                                + [(a["musteri"], tl(a["toplam_tl"])) for a in s["en_buyuk_alacaklar"][:3]])


def _cekler(s):
    return tl(s["toplam_tl"]), ([("Çek sayısı", str(s["cek_sayisi"]))]
                                + [(f"{c['musteri']} · {c['vade_tarihi']}", tl(c["tutar_tl"])) for c in s["cekler"][:3]])


# (başlık, araç, argümanlar, sonuç -> (özet, satırlar)); rolün çağıramadığı araçların bölümü atlanır
RAPOR = [
    ("Nakit pozisyonu", "nakit_pozisyonu", {}, _nakit),
    ("Önümüzdeki 7 günün ödemeleri", "yaklasan_odemeler", {"gun_sayisi": 7}, _odemeler),
    ("Açık alacaklar", "alacak_yaslandirma", {"en_fazla": 3}, _alacaklar),
    ("7 gün içinde vadesi gelen çekler", "cek_portfoyu", {"vade_gun": 7, "en_fazla": 3}, _cekler),
]


def ollama_isitici(model) -> Callable[[dt.date], None]:
    """Modelin ön belleğini o günün sistem istemi ve araç listesiyle doldurur. Sistem istemi bugünün tarihiyle
    başladığı için saat her ilerlediğinde ön bellek boşa düşer; ısıtılmazsa sonraki ilk soru ~100 sn sürer."""
    def isit(bugun: dt.date) -> None:
        model([{"role": "system", "content": SISTEM_ISTEMI.format(bugun=bugun.isoformat())},
               {"role": "user", "content": "Merhaba"}], arac_tanimlari(), 600)
    return isit


class Demo:
    def __init__(self, *, ambar: Path, durum_klasoru: Path, baslangic: Saat, anahtar: bytes, yetki: YetkiTablosu,
                 tablolar: dict[str, list[dict]], model, model_adi: str = "sahte", belge=None,
                 belge_yontemi: str = "bm25", adim_gun: int = 7, zaman: Callable[[], float] = time.time,
                 isit: Callable[[dt.date], None] | None = None):
        """tablolar: izleme kurallarının okuduğu tablolar (data/uretilen). isit: verilirse model açılışta ve her
        saat ilerleyişinde arka planda ısıtılır; verilmezse (sahte model) hazır sayılır."""
        self.klasor = Path(durum_klasoru)
        self.klasor.mkdir(parents=True, exist_ok=True)
        self.saat_yolu = self.klasor / "saat.json"
        self.saat = saat_oku(self.saat_yolu) if self.saat_yolu.exists() else baslangic
        self.anahtar, self.yetki, self.tablolar, self.zaman = anahtar, yetki, tablolar, zaman
        self.model_adi, self.adim_gun, self.isit = model_adi, adim_gun, isit
        self.depo = Depo(self.klasor / "izleme")
        durum = self.depo.durum()
        if durum and dt.date.fromisoformat(durum["son_gun"]) > self.saat.simdi:
            # Önceki çalışma depoyu yazıp saati yazamadan kesildi: saat depoya yetişir (geri alınamaz).
            self.saat = Saat(dt.date.fromisoformat(durum["son_gun"]), self.saat.veri_bitis)
        saat_yaz(self.saat, self.saat_yolu)
        calistir(tablolar, self.saat, self.depo)  # bugüne kadarki uyarılar depoya; ileri sarış yalnızca yenileri getirir
        self.sunucu = SaymanSunucusu(ambar=ambar, saat=lambda: self.saat, anahtar=anahtar, yetki=yetki,
                                     kayit=DenetimKaydi(self.klasor / "denetim.jsonl"),
                                     kapsam_klasoru=self.klasor / "kapsam", zaman=zaman, belge=belge,
                                     belge_yontemi=belge_yontemi)
        # Uyarı süzgeci, kapının kullandığı kaynaktan (ambardaki musteriler.yurtici) okunur.
        self.yurtici = {m: bool(y) for m, y in
                        self.sunucu.baglantilar["hepsi"].execute("SELECT musteri_id, yurtici FROM musteriler").fetchall()}
        self._durum_kilit = threading.RLock()  # saat ilerlerken uyarı ve rapor okunmaz (yarım dosya, iki tarih)
        # Aynı DuckDB bağlantıları hem sohbet turundan hem rapordan kullanılır: kapı çağrıları sıraya girer.
        self._db_kilit = threading.RLock()
        kapi = self.sunucu.kapi

        def sirali_kapi(ad, argumanlar, kart):
            with self._db_kilit:
                return kapi(ad, argumanlar, kart)
        self.sunucu.kapi = sirali_kapi
        self.asistan = Asistan(self.sunucu, model, Ayar(), kayit=DenetimKaydi(self.klasor / "asistan.jsonl"))
        self.kilit = threading.Lock()  # model turu ve saat ilerletme aynı anda çalışmaz
        self.oturumlar: dict[str, str] = {}
        self.model_hazir = isit is None
        self._isit()

    # -- kimlik ------------------------------------------------------------------------------------

    def ben(self, kullanici: str) -> dict:
        rol = self.yetki.kullanicilar[kullanici]
        r = self.yetki.roller[rol]
        return {"kullanici": kullanici, "ad": ADLAR.get(kullanici, kullanici), "rol": rol, "rol_adi": r.ad,
                "kapsam_adi": KAPSAM_ADI[r.kapsam]}

    def kart(self, kullanici: str) -> str:
        return kart_uret(kullanici, self.anahtar, KART_SURESI_S, self.zaman())

    # -- uyarılar ----------------------------------------------------------------------------------

    def suz(self, kullanici: str, uyarilar: list[dict]) -> list[dict]:
        """Kullanıcının görebileceği uyarılar, yalnızca gösterilecek alanlarla. Satış müdürüne giden açıklamadan
        bütün şirketin verisinden hesaplanan kısım çıkarılır (karşılıksız çekte şirketin çek eşiği, gecikmede
        müşterinin şirketteki sırası)."""
        kapsam = self.yetki.roller[self.yetki.kullanicilar[kullanici]].kapsam
        sonuc = [{k: u.get(k) for k in UYARI_ALANLARI} | {"kural": u.get("kural")} for u in uyarilar]
        if kapsam != "hepsi":
            istenen = kapsam == "yurtici"
            sonuc = [u for u in sonuc if self.yurtici.get(u["musteri_id"]) is istenen]
            for u in sonuc:
                u["aciklama"] = kapsamli_aciklama(u["kural"], u["aciklama"] or "", u["baslik"])
        for u in sonuc:
            del u["kural"]
        return sonuc

    def uyarilar(self, kullanici: str) -> list[dict]:
        with self._durum_kilit:
            gorunen = self.suz(kullanici, self.depo.uyarilar())
        return sorted(gorunen, key=lambda u: (u["gun"], u["uyari_id"]), reverse=True)[:GOSTERILEN_UYARI]

    # -- rapor -------------------------------------------------------------------------------------

    def rapor(self, kullanici: str) -> dict:
        with self._durum_kilit:
            return self._rapor(kullanici)

    def _rapor(self, kullanici: str) -> dict:
        rol = self.yetki.kullanicilar[kullanici]
        kart = self.kart(kullanici)
        bolumler = []
        for baslik, arac, argumanlar, bicim in RAPOR:
            if not self.yetki.izinli_mi(rol, arac):
                continue
            yanit, hata = self.sunucu.kapi(arac, argumanlar, kart)
            if hata:
                bolumler.append({"baslik": baslik, "ozet": "—", "satirlar": [["Durum", yanit.get("mesaj") or "alınamadı"]]})
                continue
            ozet, satirlar = bicim(yanit["sonuc"])
            bolumler.append({"baslik": baslik, "ozet": ozet, "satirlar": [list(s) for s in satirlar]})
        return {"tarih": self.saat.simdi.isoformat(), **self.ben(kullanici), "bolumler": bolumler}

    # -- saat ve model -----------------------------------------------------------------------------

    def ileri_sar(self, gun: int) -> dict:
        """Saati ilerletir, yeni günlerin uyarılarını depoya ekler. Kilit çağıranda olmalı."""
        if self.saat.simdi >= self.saat.veri_bitis:
            raise DemoHatasi(f"Verinin sonuna gelindi ({self.saat.veri_bitis.isoformat()}). Baştan başlamak için "
                             "demoyu sıfırlayın: scripts/faz10_demo.py sifirla")
        onceki, yeni = self.saat, self.saat.ileri_sar(gun)
        with self._durum_kilit:
            sonuc = calistir(self.tablolar, yeni, self.depo)  # önce izleme: hata olursa saat ilerlemez
            saat_yaz(yeni, self.saat_yolu)  # yazılamazsa açılışta saat depoya yetişir
            self.saat = yeni
        self._isit()
        return {"onceki": onceki.simdi.isoformat(), "bugun": yeni.simdi.isoformat(), "yeni": sonuc["yeni"]}

    def _isit(self) -> None:
        if self.isit is None:
            return
        self.model_hazir = False
        bugun = self.saat.simdi

        def calis():
            try:
                self.isit(bugun)
            except Exception:  # ısınamazsa yalnızca ilk soru yavaş olur
                pass
            finally:
                if self.saat.simdi == bugun:
                    self.model_hazir = True
        threading.Thread(target=calis, daemon=True).start()

    def kapat(self) -> None:
        if not getattr(self, "_kapandi", False):
            self.sunucu.kapat()
            self._kapandi = True


def uygulama_kur(demo: Demo) -> FastAPI:
    app = FastAPI(title="Sayman demo", docs_url=None, redoc_url=None, openapi_url=None)
    # Yalnızca bu bilgisayardan: başka bir ad (DNS yeniden bağlama) ile gelen istek reddedilir
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.mount("/static", StaticFiles(directory=STATIK), name="static")

    def oturum(istek: Request) -> str:
        kullanici = demo.oturumlar.get(istek.cookies.get(CEREZ, ""))
        if kullanici is None:
            raise HTTPException(401, "Önce giriş yapın.")
        return kullanici

    def kilitle():
        if not demo.kilit.acquire(blocking=False):
            raise HTTPException(409, "Sayman başka bir işi bitiriyor; birazdan yeniden deneyin.")

    @app.get("/")
    def ana_sayfa():
        return FileResponse(STATIK / "index.html")

    @app.get("/api/kullanicilar")
    def kullanicilar():
        return {"kullanicilar": [demo.ben(k) for k in demo.yetki.kullanicilar]}

    @app.post("/api/giris")
    def giris(istek: Request, yanit: Response, govde: dict = Body(...)):
        kullanici = govde.get("kullanici")
        if not isinstance(kullanici, str) or kullanici not in demo.yetki.kullanicilar:
            raise HTTPException(400, "Bilinmeyen kullanıcı.")
        demo.oturumlar.pop(istek.cookies.get(CEREZ, ""), None)  # eski oturum kapanır
        while len(demo.oturumlar) >= EN_COK_OTURUM:
            demo.oturumlar.pop(next(iter(demo.oturumlar)))
        jeton = secrets.token_urlsafe(32)
        demo.oturumlar[jeton] = kullanici
        yanit.set_cookie(CEREZ, jeton, httponly=True, samesite="strict")
        return demo.ben(kullanici)

    @app.post("/api/cikis")
    def cikis(istek: Request, yanit: Response):
        demo.oturumlar.pop(istek.cookies.get(CEREZ, ""), None)
        yanit.delete_cookie(CEREZ)
        return {"tamam": True}

    @app.get("/api/ben")
    def ben(istek: Request):
        return demo.ben(oturum(istek))

    @app.get("/api/durum")
    def durum():
        return {"bugun": demo.saat.simdi.isoformat(), "veri_bitis": demo.saat.veri_bitis.isoformat(),
                "adim_gun": demo.adim_gun, "model": demo.model_adi, "model_hazir": demo.model_hazir,
                "mesgul": demo.kilit.locked()}

    @app.get("/api/ornek_sorular")
    def ornek_sorular(istek: Request):
        rol = demo.yetki.kullanicilar[oturum(istek)]
        return {"sorular": ORNEK_SORULAR["cfo" if rol == "cfo" else "satis"]}

    @app.post("/api/sor")
    def sor(istek: Request, govde: dict = Body(...)):
        kullanici = oturum(istek)  # kimlik yalnızca oturumdan; gövdedeki başka alanlar yok sayılır
        soru = govde.get("soru")
        if not isinstance(soru, str) or not soru.strip() or len(soru) > EN_UZUN_SORU:
            raise HTTPException(422, f"Soru 1 ile {EN_UZUN_SORU} karakter arasında olmalı.")
        kilitle()
        try:
            tur = demo.asistan.sor(soru.strip(), demo.kart(kullanici))
        finally:
            demo.kilit.release()
        return {"durum": tur.durum, "cevap": tur.cevap, "sure_s": tur.sure_s,
                "araclar": [{"arac": c.get("arac"), "hata": c["yanit"].get("hata") if c.get("hata_mi") else None} for c in tur.cagrilar],
                "bugun": demo.saat.simdi.isoformat()}

    @app.post("/api/ileri_sar")
    def ileri_sar(istek: Request, govde: dict | None = Body(None)):
        kullanici = oturum(istek)
        gun = (govde or {}).get("gun", demo.adim_gun)
        if not isinstance(gun, int) or isinstance(gun, bool) or not 1 <= gun <= EN_COK_ADIM_GUN:
            raise HTTPException(422, f"Gün 1 ile {EN_COK_ADIM_GUN} arasında olmalı.")
        kilitle()
        try:
            sonuc = demo.ileri_sar(gun)
        except DemoHatasi as e:
            raise HTTPException(409, str(e)) from None
        finally:
            demo.kilit.release()
        return {"onceki": sonuc["onceki"], "bugun": sonuc["bugun"],
                "yeni_uyarilar": demo.suz(kullanici, sonuc["yeni"]), "rapor": demo.rapor(kullanici)}

    @app.get("/api/uyarilar")
    def uyarilar(istek: Request):
        return {"bugun": demo.saat.simdi.isoformat(), "uyarilar": demo.uyarilar(oturum(istek))}

    @app.get("/api/rapor")
    def rapor(istek: Request):
        return demo.rapor(oturum(istek))

    return app
