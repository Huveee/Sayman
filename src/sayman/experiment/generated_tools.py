"""Faz 9: sahte API'nin OpenAPI tanımından otomatik üretilen araçlar ve onların kapısı.

Araçlar elle yazılmaz: fastmcp.FastMCP.from_openapi her uç nokta için bir araç üretir; adı, açıklaması ve argüman
şeması OpenAPI tanımından gelir. Modele bu tanımlar olduğu gibi gösterilir (arac_tanimlari). Rol başlığı (X-Rol) bir
güvenlik şemasıdır, argümanlara girmez; başlığı kapının istemcisi koyar, model değiştiremez.

Kapı (OtomatikSunucu.kapi) elle yazılmış araçların kapısıyla (sayman.araclar.sunucu.SaymanSunucusu.kapi) aynı sırayı
izler ve aynı zarfı döndürür: kimlik kartı -> araç var mı -> argümanlar şemaya uyuyor mu -> API'yi rolün başlığıyla
çağır -> {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}. Her çağrı denetim kaydına yazılır. Farklar:
- Rol izni API'dedir: satış rollerine banka, tedarikçi, ödeme ve POS uçları 403 döner, kapı bunu "yetki_yok" yapar.
  Kapıda araç başına izin tablosu yoktur (otomatik araçlar için elle tablo yazmak elle tasarım olurdu).
- Bilinmeyen argüman kapıda reddedilir (üretilen şemalarda additionalProperties yok; model başka bir şey, ör. rol,
  gönderemesin).
- Gelecek tarih kapıda reddedilmez ("gelecek_tarih" yok); API bugünden sonraki kayıtları zaten göstermez.
- 404 (kayıt bulunamadı) ve 422 (API'nin kendi argüman kontrolü) "gecersiz_arguman" olur, mesaj API'nin mesajıdır.
- Zarfın "tarih" alanı çağrının günüdür (bugün); "kaynak" boştur (elle araçlar okunan satırları yazar).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable

import httpx
import jsonschema
from fastmcp import FastMCP

from sayman.tools import scope as kapsam_modulu
from sayman.tools.audit_log import DenetimKaydi
from sayman.tools.identity import KimlikHatasi, karti_dogrula
from sayman.tools.server import KAPSAM_ADI, MESAJLAR, Ret, _kayda_uygun
from sayman.tools.permissions import YetkiTablosu
from sayman.experiment.fake_api import Ortam, uygulama
from sayman.data.clock import Saat

ROL_BASLIGI = "X-Rol"
TIPLER = {"string": "metin", "integer": "tam sayı", "number": "sayı", "boolean": "doğru/yanlış"}
_BICIM = jsonschema.FormatChecker()

# fastmcp, API'nin her 4xx cevabını kendi günlüğüne uzun bir hata yığını olarak basar; kapı bunu zaten
# denetim kaydına yazıyor.
logging.getLogger("fastmcp").setLevel(logging.CRITICAL)


def yazma_yok(spec: dict) -> None:
    """API'de GET dışında hiçbir işlem olmamalı (Faz 3'teki "yazan araç yok" kuralı)."""
    yazan = [f"{y.upper()} {yol}" for yol, islemler in spec.get("paths", {}).items() for y in islemler
             if y.lower() not in ("get", "parameters")]
    if yazan:
        raise RuntimeError("sahte API'de yazan uç nokta var: " + ", ".join(yazan))


def _duz(o: dict) -> dict:
    """anyOf [X, null] -> X (yalnızca hata mesajı için)."""
    dal = next((a for a in o.get("anyOf", []) if a.get("type") != "null"), None)
    return o if dal is None else {**{k: v for k, v in o.items() if k != "anyOf"}, **dal}


def _sayi(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def arguman_mesaji(hata: jsonschema.ValidationError, sema: dict) -> str:
    """Şema hatasını yalnızca şemadaki adlarla anlatır (elle araçların kapısındaki mesajlarla aynı dil)."""
    ozellikler = sema.get("properties", {})
    v = hata.validator
    if v == "additionalProperties":
        return "Bilinmeyen argüman. Geçerli argümanlar: " + ", ".join(ozellikler) + "."
    if v == "required":
        return "Eksik argüman: " + ", ".join(a for a in sema.get("required", []) if a not in hata.instance) + "."
    alan = hata.path[0] if hata.path and hata.path[0] in ozellikler else None
    if alan is None:
        return "Argümanlar geçersiz."
    o = _duz(ozellikler[alan])
    if v == "anyOf":
        ic = next(iter(jsonschema.Draft202012Validator(o, format_checker=_BICIM).iter_errors(hata.instance)), None)
        v = ic.validator if ic is not None else v
    if v == "format":
        return f"{alan} YYYY-MM-DD biçiminde geçerli bir tarih olmalı."
    if v == "enum":
        return f"{alan} şunlardan biri olmalı: {', '.join(map(str, o['enum']))}."
    if v in ("minimum", "maximum"):
        if "minimum" in o and "maximum" in o:
            return f"{alan} {_sayi(o['minimum'])} ile {_sayi(o['maximum'])} arasında olmalı."
        return f"{alan} en az {_sayi(o['minimum'])} olmalı." if "minimum" in o else \
            f"{alan} en çok {_sayi(o['maximum'])} olmalı."
    if v == "type":
        return f"{alan} {TIPLER.get(o.get('type'), 'geçerli bir değer')} olmalı."
    return f"{alan} geçersiz."


def _http_hatasi(e: BaseException) -> httpx.Response | None:
    """fastmcp'nin sardığı hata zincirinde API'nin HTTP cevabı."""
    gorulen = set()
    while e is not None and id(e) not in gorulen:
        gorulen.add(id(e))
        if isinstance(e, httpx.HTTPStatusError):
            return e.response
        e = e.__cause__ or e.__context__
    return None


def _api_mesaji(cevap: httpx.Response) -> str:
    try:
        detay = cevap.json().get("detail")
    except Exception:
        detay = None
    if isinstance(detay, list):  # FastAPI 422: [{"loc": [...], "msg": "..."}]
        return "; ".join(f"{(d.get('loc') or ['?'])[-1]}: {d.get('msg', '')}" for d in detay[:3]) or "Argümanlar geçersiz."
    return str(detay) if detay else f"API {cevap.status_code} döndü."


class OtomatikSunucu:
    """SaymanSunucusu'nun yerine Asistan'a verilir: aynı özellikler (anahtar, yetki, zaman, saat) ve aynı kapi()."""

    def __init__(self, ambar: Path, saat: Callable[[], Saat], anahtar: bytes, yetki: YetkiTablosu,
                 kayit: DenetimKaydi, kapsam_klasoru: Path | None = None, zaman: Callable[[], float] = time.time):
        if not anahtar:
            raise RuntimeError("imza anahtarı olmadan sunucu açılmaz")
        self.saat, self.anahtar, self.yetki, self.kayit, self.zaman = saat, anahtar, yetki, kayit, zaman
        klasor = Path(kapsam_klasoru) if kapsam_klasoru else Path(tempfile.mkdtemp(prefix="sayman_kapsam_"))
        roller = {rol: r.kapsam for rol, r in yetki.roller.items()}
        self.baglantilar = kapsam_modulu.baglantilar(ambar, klasor, set(roller.values()))
        self.app = uygulama(Ortam(self.baglantilar, roller, lambda: self.saat().simdi))
        self.spec = self.app.openapi()
        yazma_yok(self.spec)
        self._dongu = asyncio.new_event_loop()
        self._istemciler, self._mcp = [], {}
        for rol in roller:
            istemci = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://sahte-api",
                                        headers={ROL_BASLIGI: rol}, timeout=120)
            self._istemciler.append(istemci)
            self._mcp[rol] = FastMCP.from_openapi(openapi_spec=self.spec, client=istemci, name="sahte-api")
        araclar = self._dongu.run_until_complete(self._mcp[next(iter(roller))].list_tools())
        self.semalar = {t.name: t.parameters for t in araclar}
        self.aciklamalar = {t.name: t.description or "" for t in araclar}
        # Kapı bilinmeyen argümanı reddeder; modele gösterilen şema değişmez.
        self.dogrulayicilar = {ad: jsonschema.Draft202012Validator({**s, "additionalProperties": False},
                                                                   format_checker=_BICIM)
                               for ad, s in self.semalar.items()}

    def arac_tanimlari(self) -> list[dict]:
        """Modele gösterilen araç listesi (Ollama biçimi), fastmcp'nin ürettiği gibi."""
        return [{"type": "function", "function": {"name": ad, "description": self.aciklamalar[ad], "parameters": s}}
                for ad, s in self.semalar.items()]

    def ozet(self) -> str:
        """Araç tanımlarının ve OpenAPI tanımının özeti (ölçüm satırlarına yazılır)."""
        metin = json.dumps({"araclar": self.arac_tanimlari(), "openapi": self.spec}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(metin.encode()).hexdigest()

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
            if not isinstance(ad, str) or ad not in self.semalar:
                raise Ret("bilinmeyen_arac")
            if not isinstance(argumanlar, dict):
                raise Ret("gecersiz_arguman", "Argümanlar bir nesne olmalı.")
            hata = next(iter(sorted(self.dogrulayicilar[ad].iter_errors(argumanlar), key=lambda e: list(e.path))), None)
            if hata is not None:
                raise Ret("gecersiz_arguman", "Argüman hatası: " + arguman_mesaji(hata, self.semalar[ad]))
            ozellik = self.semalar[ad].get("properties", {})
            argumanlar = {k: int(v) if isinstance(v, float) and _duz(ozellik[k]).get("type") == "integer" else v
                          for k, v in argumanlar.items() if v is not None}
            simdi = self.saat().simdi
            satir["simdi"] = simdi.isoformat()
            kapsam = self.yetki.roller[kimlik.rol].kapsam
            try:
                sonuc = self._dongu.run_until_complete(self._mcp[kimlik.rol].call_tool(ad, argumanlar))
            except Exception as e:
                cevap = _http_hatasi(e)
                if cevap is None:
                    raise
                satir["http"] = cevap.status_code
                if cevap.status_code == 403:
                    raise Ret("yetki_yok", neden="api_403") from None
                if cevap.status_code in (400, 404, 422):
                    raise Ret("gecersiz_arguman", _api_mesaji(cevap), neden=f"api_{cevap.status_code}") from None
                raise
            veri = sonuc.structured_content
            if not isinstance(veri, dict):
                raise RuntimeError("API cevabı bir nesne değil")
            yanit = {"cagri_id": cagri_id, "arac": ad, "tarih": simdi.isoformat(), "kapsam": KAPSAM_ADI[kapsam],
                     "sonuc": veri, "kaynak": {}}
            liste = next((v for v in veri.values() if isinstance(v, list)), None)
            satir.update(sonuc="tamam", tarih=simdi.isoformat(), kayit_sayisi=len(liste) if liste is not None else 1)
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
        for istemci in self._istemciler:
            self._dongu.run_until_complete(istemci.aclose())
        self._dongu.close()
        for con in self.baglantilar.values():
            con.close()


KIMLIK_TABLOLARI = {"musteri_id": "musteriler", "fatura_id": "faturalar", "iliskili_fatura_id": "faturalar",
                    "tahsilat_id": "tahsilatlar", "cek_id": "cekler", "tedarikci_id": "tedarikciler",
                    "alis_fatura_id": "alis_faturalari", "odeme_id": "odemeler", "pos_id": "pos_islemleri",
                    "hareket_id": "banka_hareketleri", "hesap_id": "banka_hesaplari"}


def kaynak_kimlikleri(sonuc) -> dict[str, list[str]]:
    """Otomatik bir sonuçtaki kayıt kimlikleri, tabloya göre (kapsam denetimi için; zarfa konmaz, modele gitmez).

    Elle araçların zarfındaki "kaynak" alanının karşılığıdır: puanlayıcının kapsam denetimi (puanlama.kapsam_ihlalleri)
    "kaynak"a bakar; otomatik zarfta "kaynak" boş olduğu için Faz 9 analizi kimlikleri sonucun kendisinden toplar."""
    tablolar: dict[str, list[str]] = {}

    def gez(v):
        if isinstance(v, dict):
            for k, w in v.items():
                if k in KIMLIK_TABLOLARI and isinstance(w, str):
                    tablolar.setdefault(KIMLIK_TABLOLARI[k], []).append(w)
                else:
                    gez(w)
        elif isinstance(v, list):
            for w in v:
                gez(w)

    gez(sonuc)
    return {t: sorted(set(k)) for t, k in tablolar.items()}


def kol_ozeti() -> str:
    """Otomatik kolun parmak izi: OpenAPI tanımı, fastmcp sürümü ve deney dosyaları (ölçüm satırlarına yazılır)."""
    import sys

    import fastmcp

    from sayman.experiment import oracle, fake_api
    spec = uygulama(Ortam({}, {}, lambda: None)).openapi()
    parcalar = [json.dumps(spec, ensure_ascii=False, sort_keys=True), fastmcp.__version__]
    parcalar += [Path(m.__file__).read_text(encoding="utf-8") for m in (fake_api, sys.modules[__name__], oracle)]
    return hashlib.sha256("\n".join(parcalar).encode()).hexdigest()
