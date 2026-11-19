"""Orkestratör: soru → model ile araçlar arasında döngü → doğrulanmış cevap ya da sabit bir metin.

Değişmez kural: kullanıcıya gösterilen metin ya doğrulayıcıdan geçmiş bir model cevabıdır ya da aşağıdaki sabit
metinlerden biridir (METINLER). Reddedilen taslak hiçbir zaman gösterilmez.

Bir tur şu durumlardan biriyle biter:
- tamam: cevap doğrulandı.
- ret: kimlik kartı geçersiz ya da bu turdaki araç çağrılarının hepsi "yetki_yok" ile reddedildi. Sabit metin döner
  ve modele bir daha gidilmez: model reddedilen bilgiyi tahminle ya da başka sözlerle anlatmaya çalışamaz. Yalnızca
  "yetki_yok" sayılır; şema hatası, bilinmeyen araç ya da araç hatası modele geri verilir, model düzeltebilir.
  Aynı adımda bir çağrı reddedilip bir başkası başarılı olursa döngü sürer.
- uretemedim: cevap doğrulanamadı, modelden bir kez düzeltmesi istendi, yine doğrulanamadı. Boş ya da yarıda
  kesilmiş (üretim sınırına takılmış) cevap da doğrulanamamış sayılır: kesik bir sayı yuvarlama kuralından geçebilirdi.
- adim_siniri: model çağrısı (düzeltme dahil) ya da araç çağrısı sınırı doldu. Düzeltme gerekirken adım kalmamışsa
  sonuç adim_siniri olur: düzeltme için bir model çağrısı daha gerekir.
- sure_siniri: süre doldu. Her model çağrısından önce ve sonra bakılır; modele kalan süre zaman aşımı olarak verilir.
- baglam_siniri: mesajlar modelin bağlam penceresine sığmayacak kadar büyüdü. Ollama sığmayan baştaki metni sessizce
  keser; o zaman doğrulayıcı, modelin artık görmediği sonuçlardaki sayıları kabul ederdi. Model çağrısından önce
  tahmin edilir: modelin bir önceki çağrıda bildirdiği istem token sayısına (Ollama ön bellekten okunan kısmı da
  sayıyor) yalnızca yeni mesajların karakter tahmini eklenir (Faz 3 ölçümü: araç sonuçlarında ~1,8 karakter/token;
  burada ihtiyatlı 1,7; belge araması sonuçlarında Türkçe madde metni ölçülen 3,1-3,2, burada 2,8). Çağrıdan sonra
  da modelin bildirdiği sayıya bakılır.
- model_hatasi: model çağrısı hata verdi (ör. Ollama kapalı).

Kimlik kartı mesajlara girmez, yalnızca kapıya gider. Kart turun başında, süre sınırının dolacağı an için doğrulanır:
tur sürerken kartın süresi dolamaz.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from sayman.araclar.araclar import ARACLAR
from sayman.araclar.kayit import DenetimKaydi
from sayman.araclar.kimlik import KimlikHatasi, karti_dogrula
from sayman.asistan.dogrulayici import Dogrulama, dogrula

METINLER = {
    "kimlik": "Kimlik doğrulanamadı.",
    "ret": "Bu bilgiyi görme yetkiniz yok.",
    "uretemedim": "Bu soruya doğrulanabilir bir cevap üretemedim: cevaptaki bazı sayılar ya da alıntılar araç "
                  "sonuçlarıyla eşleşmedi. Soruyu daha dar sorarak yeniden deneyebilirsiniz.",
    "adim_siniri": "Bu soru izin verilen adım sayısı içinde cevaplanamadı. Soruyu daha küçük parçalara bölerek "
                   "sorabilirsiniz.",
    "sure_siniri": "Bu soru süre sınırı içinde cevaplanamadı. Biraz sonra yeniden deneyebilir ya da soruyu "
                   "daraltabilirsiniz.",
    "baglam_siniri": "Bu soru tek seferde işlenemeyecek kadar çok veri gerektirdi. Soruyu daha küçük parçalara "
                     "bölerek sorabilirsiniz.",
    "model_hatasi": "Dil modeline şu an ulaşılamıyor. Biraz sonra yeniden deneyin.",
}

SISTEM_ISTEMI = """Sen Sayman'sın: bir şirketin yapay zekâ destekli finans asistanısın. Bugünün tarihi {bugun}.
Kurallar:
1. Sorulara yalnızca araçların bu konuşmada döndürdüğü verilerle cevap ver. Gereken veriyi her zaman araçlardan al; rakam uydurma.
2. Sayıları araç sonucundan aynen al. Türkçe biçimde yazabilirsin (75826754.65 → 75.826.754,65 TL) ya da yuvarlayabilirsin (75,8 milyon TL).
3. Kendin toplama, çıkarma, yüzde ya da oran hesaplaması yapma; araç sonucunda olmayan bir sayı yazma.
4. Sözleşme, şirket politikası ya da kanun sorularında belge_ara aracını kullan. belge_ara kullandığında cevabında en az bir alıntı olmalı: maddeden en az üç kelimelik bir bölümü çift tırnak içinde aynen yaz ve hemen ardından parça kimliğini köşeli parantezle ver. Örnek: "fatura tarihinden itibaren 30 gün içinde" [SOZ-S001#m3]
5. Çift tırnağı yalnızca belge alıntısı için kullan; müşteri ya da şirket adlarını tırnak içine alma.
6. Bir araç "yetki_yok" derse bu bilgiyi veremeyeceğini söyle; başka araçla bulmaya çalışma.
7. Sayman salt okunurdur: ödeme, transfer ya da kayıt değişikliği yapamaz.
Kısa ve net Türkçe cevap ver: en çok 120 kelime; listelerde en çok 5 kalem; tablo kullanma."""

ALINTI_NEDENLERI = {
    "bilinmeyen_parca": "bu parça bu konuşmada belge_ara ile getirilmedi",
    "cok_kisa": "alıntı en az üç kelime olmalı",
    "metinde_yok": "parçanın metninde aynen geçmiyor",
    "baska_parcada": "bu parçada değil, başka bir parçada geçiyor",
    "alintisiz_atif": "parça kimliği tırnaklı bir alıntı olmadan verilmiş",
    "kimliksiz_alinti": "tırnaklı metnin ardından parça kimliği yok (çift tırnak yalnızca alıntı içindir)",
}


@dataclass(frozen=True)
class Ayar:
    en_cok_adim: int = 6             # model çağrısı, düzeltme dahil
    en_cok_cagri: int = 8            # araç çağrısı
    duzeltme: int = 1                # doğrulanamayan cevap için kaç kez düzeltme istenir
    # Faz 0 ölçümü (bu bilgisayar, ekran kartsız): qwen3.5:9b istemi saniyede ~27 token okuyor, ~5 token yazıyor.
    # Üç model çağrılı bir tur: ~3.000 yeni istem tokenı (~110 sn) + 512 tokenlık cevap (~100 sn) + araç çağrıları
    # (~25 sn) ≈ 235 sn. Sınır 300 sn; daha hızlı bir bilgisayarda düşürülebilir.
    sure_s: float = 300.0
    baglam_token: int = 8192         # modelin num_ctx değeri
    cevap_payi: int = 512            # modelin num_predict değeri
    arac_listesi_token: int = 2400   # Faz 3: 13 araç 2.187 token; belge_ara ile yeniden ölçülür (faz6_duman.py)
    karakter_token: float = 1.7      # mesajlarda token tahmini için karakter/token (JSON araç sonuçları: ölçülen ~1,8)
    belge_karakter_token: float = 2.8  # belge_ara sonuçları (Türkçe madde metni: ölçülen 3,1-3,2)


@dataclass
class ModelCevabi:
    icerik: str = ""
    arac_cagrilari: list[tuple[str, object]] = field(default_factory=list)
    kesildi: bool = False            # üretim sınırına takıldı (Ollama done_reason == "length")
    istem_token: int | None = None
    uretim_token: int | None = None
    sure_s: float | None = None


class ModelZamanAsimi(Exception):
    """Model çağrısı kendisine verilen sürede bitmedi."""


@dataclass
class Tur:
    tur_id: str
    durum: str
    cevap: str                                    # kullanıcıya gösterilen metin
    kullanici: str | None = None
    cagrilar: list[dict] = field(default_factory=list)
    taslaklar: list[str] = field(default_factory=list)          # modelin son cevap taslakları (gösterilmez)
    dogrulamalar: list[Dogrulama] = field(default_factory=list)  # her taslağın doğrulaması
    model_cagrisi: int = 0
    tokenlar: list[dict] = field(default_factory=list)
    sure_s: float = 0.0
    neden: str | None = None


def arac_tanimlari() -> list[dict]:
    return [{"type": "function", "function": {"name": a.ad, "description": a.aciklama, "parameters": a.sema}}
            for a in ARACLAR]


def duzeltme_mesaji(taslak: ModelCevabi, d: Dogrulama) -> str | None:
    """Taslakta düzeltilecek bir şey varsa modele gidecek mesaj; yoksa None."""
    satirlar = []
    if not taslak.icerik.strip():
        satirlar.append("- Cevap boş geldi.")
    if taslak.kesildi:
        satirlar.append("- Cevap uzunluk sınırına takılıp yarıda kesildi; daha kısa yaz.")
    if d.kaynaksiz:
        satirlar.append("- Şu sayılar bu konuşmadaki araç sonuçlarında yok: "
                        + "; ".join(s.metin for s in d.kaynaksiz) + ".")
    for a in d.gecersiz_alintilar:
        ne = f'"{a.alinti}"' if a.alinti else "alıntı"
        satirlar.append(f"- {ne} [{a.parca_id}]: {ALINTI_NEDENLERI.get(a.neden, a.neden)}.")
    if d.alinti_eksik:
        satirlar.append('- belge_ara kullandın ama cevapta geçerli bir alıntı yok: getirilen bir maddeden en az üç '
                        'kelimeyi çift tırnak içinde aynen yaz ve hemen ardından parça kimliğini köşeli parantezle ver '
                        '(ör. "..." [SOZ-S001#m3]).')
    if not satirlar:
        return None
    return ("Cevabın doğrulanamadı:\n" + "\n".join(satirlar) + "\nCevabı yeniden yaz: yalnızca araç sonuçlarındaki "
            "sayıları kullan, kendin hesaplama yapma. Gerekirse aracı yeniden çağır.")


class Asistan:
    def __init__(self, sunucu, model: Callable[[list[dict], list[dict], float], ModelCevabi], ayar: Ayar = Ayar(),
                 saat: Callable[[], float] = time.monotonic, kayit: DenetimKaydi | None = None):
        """sunucu: SaymanSunucusu (araçlar yalnızca onun kapısından çağrılır). model: (mesajlar, araç tanımları,
        zaman aşımı saniyesi) -> ModelCevabi. kayit: tur özetlerinin yazılacağı ayrı bir denetim kaydı (isteğe bağlı;
        araç çağrıları zaten sunucunun kaydına yazılır)."""
        self.sunucu, self.model, self.ayar, self.saat, self.kayit = sunucu, model, ayar, saat, kayit
        self.araclar = arac_tanimlari()

    def _token_tahmini(self, mesajlar: list[dict], bilinen: tuple[int, int] | None = None) -> int:
        """Bir sonraki istemin token tahmini. Model bir önceki çağrıda istem token sayısını bildirdiyse o sayı
        (artı ürettiği tokenlar) esas alınır, yalnızca sonradan eklenen mesajlar karakterden tahmin edilir; yoksa
        araç listesi ölçümü artı bütün mesajların karakter tahmini kullanılır."""
        def tahmin(liste):
            return int(sum(len(json.dumps(m, ensure_ascii=False))
                           / (self.ayar.belge_karakter_token if m.get("tool_name") == "belge_ara"
                              else self.ayar.karakter_token) for m in liste))

        if bilinen is not None:
            token, once = bilinen
            return token + tahmin(mesajlar[once:])
        return self.ayar.arac_listesi_token + tahmin(mesajlar)

    def sor(self, soru: str, kart) -> Tur:
        a, s = self.ayar, self.sunucu
        bas = self.saat()
        son_an = bas + a.sure_s
        tur = Tur(tur_id="T" + secrets.token_hex(6), durum="", cevap="")

        def bitir(durum: str, cevap: str | None = None, neden: str | None = None) -> Tur:
            tur.durum, tur.neden = durum, neden
            tur.cevap = cevap if cevap is not None else METINLER[durum]
            tur.sure_s = round(self.saat() - bas, 3)
            if self.kayit is not None:
                son = tur.dogrulamalar[-1] if tur.dogrulamalar else None
                self.kayit.yaz(tur_id=tur.tur_id, kullanici=tur.kullanici, soru=soru[:500], durum=durum,
                               neden=neden, model_cagrisi=tur.model_cagrisi,
                               cagri_idleri=[c["yanit"].get("cagri_id") for c in tur.cagrilar],
                               taslak_sayisi=len(tur.taslaklar),
                               kaynaksiz=[x.metin for x in son.kaynaksiz] if son else [],
                               gecersiz_alinti=[x.neden for x in son.gecersiz_alintilar] if son else [],
                               istem_token=[t["istem"] for t in tur.tokenlar], sure_s=tur.sure_s)
            return tur

        try:
            kimlik = karti_dogrula(kart, s.anahtar, s.yetki.kullanicilar, s.zaman() + a.sure_s)
        except KimlikHatasi as e:
            return bitir("ret", METINLER["kimlik"], neden=e.neden)
        tur.kullanici = kimlik.kullanici
        bugun: dt.date = s.saat().simdi
        mesajlar = [{"role": "system", "content": SISTEM_ISTEMI.format(bugun=bugun.isoformat())},
                    {"role": "user", "content": soru}]
        duzeltme = 0
        bilinen = None  # (modelin son bildirdiği istem + üretim tokenı, o çağrıda kaç mesaj vardı)
        while True:
            if tur.model_cagrisi >= a.en_cok_adim:
                return bitir("adim_siniri", neden="model_cagrisi")
            kalan = son_an - self.saat()
            if kalan <= 0:
                return bitir("sure_siniri")
            if self._token_tahmini(mesajlar, bilinen) > a.baglam_token - a.cevap_payi:
                return bitir("baglam_siniri")
            mesaj_sayisi = len(mesajlar)
            try:
                c = self.model(mesajlar, self.araclar, kalan)
            except ModelZamanAsimi:
                return bitir("sure_siniri")
            except Exception as e:  # ayrıntı yalnızca kayda
                return bitir("model_hatasi", neden=f"{type(e).__name__}: {str(e)[:200]}")
            tur.model_cagrisi += 1
            tur.tokenlar.append({"istem": c.istem_token, "uretim": c.uretim_token, "sure_s": c.sure_s})
            if self.saat() > son_an:
                return bitir("sure_siniri")
            if c.istem_token is not None and c.istem_token > a.baglam_token - a.cevap_payi:
                return bitir("baglam_siniri", neden="istem_token")  # tahmin tutmadı; model kesik bağlam görmüş olabilir
            if c.istem_token is not None:
                bilinen = (c.istem_token + (c.uretim_token or 0), mesaj_sayisi)

            if c.arac_cagrilari:
                if len(tur.cagrilar) + len(c.arac_cagrilari) > a.en_cok_cagri:
                    return bitir("adim_siniri", neden="arac_cagrisi")
                mesajlar.append({"role": "assistant", "content": c.icerik,
                                 "tool_calls": [{"function": {"name": ad, "arguments": arg}}
                                                for ad, arg in c.arac_cagrilari]})
                for ad, arg in c.arac_cagrilari:
                    yanit, hata = s.kapi(ad, arg, kart)
                    tur.cagrilar.append({"arac": ad, "argumanlar": arg if isinstance(arg, dict) else {},
                                         "hata_mi": hata, "yanit": yanit})
                    mesajlar.append({"role": "tool", "tool_name": ad if isinstance(ad, str) else repr(ad),
                                     "content": json.dumps(yanit, ensure_ascii=False)})
                if all(x["hata_mi"] and x["yanit"].get("hata") == "yetki_yok" for x in tur.cagrilar):
                    return bitir("ret", neden="yetki_yok")
                continue

            d = dogrula(c.icerik, soru, tur.cagrilar, bugun)
            tur.taslaklar.append(c.icerik)
            tur.dogrulamalar.append(d)
            mesaj = duzeltme_mesaji(c, d)
            if mesaj is None:
                return bitir("tamam", c.icerik.strip())
            if duzeltme >= a.duzeltme:
                return bitir("uretemedim")
            duzeltme += 1
            mesajlar += [{"role": "assistant", "content": c.icerik}, {"role": "user", "content": mesaj}]
