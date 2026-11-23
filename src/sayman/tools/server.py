"""Sayman MCP sunucusu: araçları modele sunar; her çağrı tek bir kapıdan geçer.

Kapıdaki sıra: kimlik kartı -> araç var mı -> rol bu aracı çağırabilir mi -> argümanlar şemaya uyuyor mu ->
tarih "bugün"ü geçiyor mu -> aracı rolün kapsamındaki salt okunur bağlantıda çalıştır. Sonuç ne olursa olsun
(başarılı, reddedildi, hata) çağrı denetim kaydına yazılır; kimlik kartı yazılmaz.

MCP kütüphanesinin kendi girdi kontrolü kapalıdır (validate_input=False): açık olsaydı hatalı argümanlı
bir istek kimlik kontrolüne ve denetim kaydına uğramadan geri dönerdi. Kontrolü kapı yapar.

Çalıştırma (stdio, proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz3_sunucu.py
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server

from sayman.tools import scope as kapsam_modulu
from sayman.tools.catalog import ARACLAR, ARACLAR_ADA_GORE, AracHatasi, AracKullanilamaz, Baglam
from sayman.tools.audit_log import DenetimKaydi
from sayman.tools.identity import KimlikHatasi, karti_dogrula, metadan_kart
from sayman.tools.permissions import YetkiTablosu
from sayman.data.clock import Saat

KAPSAM_ADI = {"hepsi": "tüm şirket", "yurtici": "yalnızca yurtiçi müşteriler", "ihracat": "yalnızca yurt dışı müşteriler"}
MESAJLAR = {
    "kimlik_gecersiz": "Kimlik doğrulanamadı.",
    "bilinmeyen_arac": "Böyle bir araç yok.",
    "yetki_yok": "Bu bilgiyi görme yetkiniz yok.",
    "hata": "Araç çalışırken bir hata oluştu.",
    "kullanilamaz": "Bu araç şu an kullanılamıyor.",
}
EN_UZUN_ARGUMAN = 2000  # denetim kaydına yazılan argüman metninin üst sınırı


class Ret(Exception):
    def __init__(self, kod: str, mesaj: str | None = None, neden: str | None = None):
        super().__init__(kod)
        self.kod, self.mesaj, self.neden = kod, mesaj or MESAJLAR[kod], neden or kod


def _kayda_uygun(argumanlar) -> object:
    metin = json.dumps(argumanlar, ensure_ascii=False, default=str)
    return argumanlar if len(metin) <= EN_UZUN_ARGUMAN else metin[:EN_UZUN_ARGUMAN] + "…"


TIPLER = {"string": "metin", "integer": "tam sayı"}


def arguman_mesaji(hata: jsonschema.ValidationError, sema: dict) -> str:
    """Şema hatasını yalnızca şemadaki adlarla anlatır; kullanıcının girdisini geri yansıtmaz (cevap kısa kalır)."""
    ozellikler = sema["properties"]
    alan = hata.path[0] if hata.path and hata.path[0] in ozellikler else None
    v = hata.validator
    if v == "additionalProperties":
        return "Bilinmeyen argüman. Geçerli argümanlar: " + ", ".join(ozellikler) + "."
    if v == "required":
        return "Eksik argüman: " + ", ".join(a for a in sema["required"] if a not in hata.instance) + "."
    if alan is None:
        return "Argümanlar geçersiz."
    o = ozellikler[alan]
    if v == "format":
        return f"{alan} YYYY-MM-DD biçiminde geçerli bir tarih olmalı."
    if v == "enum":
        return f"{alan} şunlardan biri olmalı: {', '.join(o['enum'])}."
    if v == "maximum":  # Faz 9 üçüncü kol: sınırı aşan model ne yapacağını bilsin
        return f"{alan} en çok {o['maximum']} olabilir; {o['maximum']} ile yeniden deneyin."
    if v == "minimum":
        return f"{alan} {o['minimum']} ile {o['maximum']} arasında olmalı."
    if v == "maxLength":
        return f"{alan} en çok {o['maxLength']} karakter olabilir."
    if v == "minLength":
        return f"{alan} en az {o['minLength']} karakter olmalı."
    if v == "type":
        return f"{alan} {TIPLER.get(o.get('type'), 'geçerli bir değer')} olmalı."
    return f"{alan} geçersiz."


def _gelecek_mi(deger, simdi: dt.date) -> bool:
    return isinstance(deger, str) and dt.date.fromisoformat(deger) > simdi


class SaymanSunucusu:
    def __init__(self, ambar: Path, saat: Callable[[], Saat], anahtar: bytes, yetki: YetkiTablosu,
                 kayit: DenetimKaydi, kapsam_klasoru: Path | None = None, zaman: Callable[[], float] = time.time,
                 belge=None, belge_yontemi: str = "anlam"):
        """belge: belge arayıcısı (verilmezse belge_ara "kullanilamaz" ile reddedilir). belge_yontemi: "anlam"
        (varsayılan), "bm25", "karma" ya da "karma_sirala" (sıralayıcı; daha güçlü bir bilgisayar için)."""
        if not anahtar:
            raise RuntimeError("imza anahtarı olmadan sunucu açılmaz")
        self.saat, self.anahtar, self.yetki, self.kayit, self.zaman = saat, anahtar, yetki, kayit, zaman
        self.belge, self.belge_yontemi = belge, belge_yontemi
        klasor = Path(kapsam_klasoru) if kapsam_klasoru else Path(tempfile.mkdtemp(prefix="sayman_kapsam_"))
        self.baglantilar = kapsam_modulu.baglantilar(ambar, klasor, {r.kapsam for r in yetki.roller.values()})
        self.dogrulayicilar = {a.ad: jsonschema.Draft202012Validator(a.sema, format_checker=jsonschema.FormatChecker())
                               for a in ARACLAR}
        self.mcp = Server("sayman")
        self.mcp.list_tools()(self._listele)
        self.mcp.call_tool(validate_input=False)(self._cagir)

    # -- MCP uçları -------------------------------------------------------------------------------

    @staticmethod
    def araclar() -> list[types.Tool]:
        return [types.Tool(name=a.ad, description=a.aciklama, inputSchema=a.sema,
                           annotations=types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                                             idempotentHint=True, openWorldHint=False))
                for a in ARACLAR]

    async def _listele(self) -> list[types.Tool]:
        return self.araclar()

    async def _cagir(self, ad: str, argumanlar: dict) -> types.CallToolResult:
        try:
            meta = self.mcp.request_context.meta
        except LookupError:
            meta = None
        yanit, hata = self.kapi(ad, argumanlar, metadan_kart(meta))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(yanit, ensure_ascii=False))],
                                    structuredContent=yanit, isError=hata)

    # -- kapı ---------------------------------------------------------------------------------------

    def kapi(self, ad, argumanlar, kart) -> tuple[dict, bool]:
        """Tek kapı. (yanıt, hata_mı) döner; her durumda denetim kaydına bir satır yazar."""
        cagri_id = "C" + secrets.token_hex(6)
        bas = time.perf_counter()
        satir = {"cagri_id": cagri_id, "arac": (ad if isinstance(ad, str) else repr(ad))[:100],
                 "argumanlar": _kayda_uygun(argumanlar), "kullanici": None, "rol": None}
        try:
            try:
                kimlik = karti_dogrula(kart, self.anahtar, self.yetki.kullanicilar, self.zaman())
            except KimlikHatasi as e:
                raise Ret("kimlik_gecersiz", neden=e.neden) from None
            satir.update(kullanici=kimlik.kullanici, rol=kimlik.rol)
            arac = ARACLAR_ADA_GORE.get(ad)
            if arac is None:
                raise Ret("bilinmeyen_arac")
            if not self.yetki.izinli_mi(kimlik.rol, ad):
                raise Ret("yetki_yok")
            if not isinstance(argumanlar, dict):
                raise Ret("gecersiz_arguman", "Argümanlar bir nesne olmalı.")
            hata = next(iter(sorted(self.dogrulayicilar[ad].iter_errors(argumanlar), key=lambda e: list(e.path))), None)
            if hata is not None:
                raise Ret("gecersiz_arguman", "Argüman hatası: " + arguman_mesaji(hata, arac.sema))
            # JSON şeması 5.0'ı da tam sayı sayar; araçlar gerçek int alsın.
            argumanlar = {k: int(v) if isinstance(v, float) and arac.ozellikler[k].get("type") == "integer" else v
                          for k, v in argumanlar.items()}
            simdi = self.saat().simdi
            satir["simdi"] = simdi.isoformat()  # simülasyon günü: çağrı bu satırdan aynen yeniden üretilebilir
            for alan in arac.tarih_alanlari:
                if _gelecek_mi(argumanlar.get(alan), simdi):
                    raise Ret("gelecek_tarih", f"Bugünden ({simdi.isoformat()}) sonraki günler için veri yok.")
            kapsam = self.yetki.roller[kimlik.rol].kapsam
            baglam = Baglam(self.baglantilar[kapsam], simdi, kimlik.rol, self.belge, self.belge_yontemi)
            try:
                tarih, sonuc, kaynak = arac.calistir(baglam, argumanlar)
            except AracHatasi as e:
                raise Ret("gecersiz_arguman", str(e)) from None
            except AracKullanilamaz as e:
                raise Ret("kullanilamaz", str(e)) from None
            yanit = {"cagri_id": cagri_id, "arac": ad, "tarih": tarih.isoformat(), "kapsam": KAPSAM_ADI[kapsam],
                     "sonuc": sonuc, "kaynak": kaynak}
            satir.update(sonuc="tamam", tarih=tarih.isoformat(), kayit_sayisi=sum(len(v) for v in kaynak.values()))
            return yanit, False
        except Ret as r:
            satir.update(sonuc="reddedildi", neden=r.neden)
            return {"cagri_id": cagri_id, "hata": r.kod, "mesaj": r.mesaj}, True
        except Exception as e:  # beklenmeyen hata: ayrıntı yalnızca kayda
            satir.update(sonuc="hata", neden=f"{type(e).__name__}: {str(e)[:300]}")
            return {"cagri_id": cagri_id, "hata": "hata", "mesaj": MESAJLAR["hata"]}, True
        finally:
            satir["sure_ms"] = round((time.perf_counter() - bas) * 1000, 1)
            self.kayit.yaz(**satir)

    def kapat(self) -> None:
        for con in self.baglantilar.values():
            con.close()


def main(argv: list[str] | None = None) -> None:
    """stdio sunucusu. Anahtar önce ortam değişkeninden, yoksa proje kökündeki .env dosyasından okunur."""
    import argparse
    import os

    import anyio
    from dotenv import dotenv_values
    from mcp.server.stdio import stdio_server

    from sayman.tools.identity import ANAHTAR_DEGISKENI, anahtar_oku
    from sayman.tools.permissions import yetki_oku
    from sayman.documents.search import YONTEMLER, arayici_kur
    from sayman.data.clock import saat_oku

    kok = Path(__file__).resolve().parents[3]
    ap = argparse.ArgumentParser(description="Sayman MCP sunucusu (stdio)")
    ap.add_argument("--ambar", type=Path, default=kok / "data" / "ambar" / "sayman.duckdb")
    ap.add_argument("--saat", type=Path, default=kok / "data" / "saat.json")
    ap.add_argument("--kayit", type=Path, default=kok / "data" / "denetim" / "kayit.jsonl")
    ap.add_argument("--kapsam-klasoru", type=Path, default=kok / "data" / "ambar" / "kapsam")
    ap.add_argument("--belge", type=Path, default=None,
                    help="Faz 5 belge kütüphanesi klasörü (ör. data/belge); verilmezse belge_ara kapalıdır")
    ap.add_argument("--belge-yontemi", choices=YONTEMLER, default="anlam",
                    help="karma_sirala sıralayıcı modeli yükler; daha güçlü bir bilgisayar için")
    ap.add_argument("--siralayici-modeli", type=Path, default=kok / "data" / "modeller" / "bge-reranker-v2-m3")
    a = ap.parse_args(argv)
    belge = arayici_kur(a.belge, a.belge_yontemi, a.siralayici_modeli) if a.belge else None
    ortam = dict(dotenv_values(kok / ".env"))
    if os.environ.get(ANAHTAR_DEGISKENI):
        ortam[ANAHTAR_DEGISKENI] = os.environ[ANAHTAR_DEGISKENI]
    sunucu = SaymanSunucusu(ambar=a.ambar, saat=lambda: saat_oku(a.saat), anahtar=anahtar_oku(ortam),
                            yetki=yetki_oku(), kayit=DenetimKaydi(a.kayit), kapsam_klasoru=a.kapsam_klasoru,
                            belge=belge, belge_yontemi=a.belge_yontemi)

    async def calistir():
        async with stdio_server() as (oku, yaz):
            await sunucu.mcp.run(oku, yaz, sunucu.mcp.create_initialization_options())

    try:
        anyio.run(calistir)
    finally:
        sunucu.kapat()
