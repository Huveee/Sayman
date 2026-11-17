"""Faz 6: gerçek modelle küçük duman testi ve bağlam ölçümleri (yazılım testlerinden ayrı raporlanır).

1. Ölçüm: 14 aracın listesi kaç token tutuyor, en uzun belge araması sonucu kaç token tutuyor, Ollama'nın
   prompt_eval_count değeri ön bellekten etkileniyor mu. Ön belleğin karışmaması için her ölçüm isteminin başına
   rastgele bir ek konur.
2. Duman testi: sabit 13 soru, üç rol, gerçek ambar ve belge kütüphanesi (anlam araması), gerçek kapı. Her soru için
   bitiş durumu, gösterilen cevap, araç çağrıları, reddedilen taslaklar ve token sayıları yazılır.

Model: qwen3.5:9b, sıcaklık 0, tohum 42, düşünme kapalı. Kimlik kartları bu çalıştırmaya özel rastgele bir anahtarla
imzalanır; .env'deki anahtar kullanılmaz. Denetim kayıtları geçici klasöre yazılır.

Girdi:  data/ambar/sayman.duckdb, data/saat.json, data/belge/ (belgeler.jsonl ve gömme önbelleği)
Çıktı:  reports/faz6_duman.json (--sorular verilirse reports/faz6_duman_secili.json; ana rapor değişmez)

Kullanım (proje kökünden; Ollama açık olmalı):
    .venv\\Scripts\\python -I scripts\\faz6_duman.py
    .venv\\Scripts\\python -I scripts\\faz6_duman.py --sorular 9 --not "neden yeniden soruldu"
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.araclar.araclar import BELGE_EN_FAZLA, BELGE_METIN_SINIRI, _kisalt  # noqa: E402
from sayman.araclar.kayit import DenetimKaydi  # noqa: E402
from sayman.araclar.kimlik import kart_uret  # noqa: E402
from sayman.araclar.sunucu import SaymanSunucusu  # noqa: E402
from sayman.araclar.yetki import yetki_oku  # noqa: E402
from sayman.asistan.model import MODEL, SECENEKLER, OllamaModeli  # noqa: E402
from sayman.asistan.orkestrator import Asistan, Ayar, arac_tanimlari  # noqa: E402
from sayman.belge.arama import arayici_kur  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

RAPOR = KOK / "reports" / "faz6_duman.json"
NOT = ("İkinci çalıştırma. İlk çalıştırma (reports/faz6_duman_ilk.json, süre sınırı 120 sn) doğrulayıcıda üç açık "
       "gösterdi (\"TL**\" biçim işareti, \"09-15 Eylül\" tarih aralığı, kısa rakamsız tırnaklı söz); bunlar kapatıldı "
       "ve süre sınırı Faz 0 hızından 300 sn'ye çıkarıldı. Sorular aynıdır: bu bağımsız bir ölçüm değil, duman testidir.")
CFO, YURTICI, IHRACAT = "selin.aydin", "murat.kaya", "deniz.arslan"
SORULAR = [
    (CFO, "Bankalarda toplam ne kadar nakitimiz var?"),
    (CFO, "Son 90 günün DSO'su kaç?"),
    (CFO, "Green Home Store Ltd'nin durumu nasıl?"),
    (CFO, "Vadesini 60 günden fazla geçmiş kaç fatura var, toplamı ne kadar?"),
    (CFO, "Kur riskimiz ne durumda?"),
    (CFO, "Önümüzdeki 30 günde ne kadar tedarikçi ödememiz var?"),
    (CFO, "Önümüzdeki 4 haftanın nakit akışı nasıl görünüyor?"),
    (CFO, "Bereket İmalat'a faturaları kaç günde ödüyoruz?"),
    (CFO, "Ticari defterleri kaç yıl saklamamız gerekiyor?"),
    (CFO, "EUR ve USD pozisyonlarımızın TL karşılığı toplam ne kadar?"),
    (YURTICI, "Bankada ne kadar paramız var?"),
    (YURTICI, "Vadesi geçmiş alacaklarımız ne durumda?"),
    (IHRACAT, "En büyük alacaklarımız hangi müşterilerde?"),
]


def _istem_token(istemci, mesajlar, araclar=None) -> int:
    r = istemci.chat(model=MODEL, messages=mesajlar, tools=araclar, think=False, keep_alive="15m",
                     options={**SECENEKLER, "num_predict": 1})
    return r.prompt_eval_count


def olcumler(belge) -> dict:
    import ollama
    istemci = ollama.Client(timeout=600)

    def sistem():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] Sen bir finans asistanısın."}

    bas = time.perf_counter()
    _istem_token(istemci, [sistem(), {"role": "user", "content": "Merhaba"}])  # model yüklensin
    yukleme_s = round(time.perf_counter() - bas, 1)
    araclar = arac_tanimlari()
    bos = _istem_token(istemci, [sistem(), {"role": "user", "content": "Merhaba"}])
    aracli = _istem_token(istemci, [sistem(), {"role": "user", "content": "Merhaba"}], araclar)
    ayni = [sistem(), {"role": "user", "content": "Merhaba"}]
    tekrar = [_istem_token(istemci, ayni, araclar), _istem_token(istemci, ayni, araclar)]

    # En kötü belge sonucu: CFO'nun görebildiği en uzun 3 madde, araçtaki sınırla kesilmiş.
    en_uzun = sorted((p for p in belge.parca.values() if "cfo" in p.roller), key=lambda p: -len(p.metin))
    sonuclar = []
    for p in en_uzun[:BELGE_EN_FAZLA]:
        metin, kesildi = _kisalt(p.metin, BELGE_METIN_SINIRI)
        sonuclar.append({"parca_id": p.parca_id, "belge": p.belge_baslik, "madde": p.madde_baslik, "metin": metin,
                         **({"metin_kesildi": True} if kesildi else {})})
    yanit = json.dumps({"cagri_id": "C000000000000", "arac": "belge_ara", "tarih": "2026-09-01",
                        "kapsam": "tüm şirket", "sonuc": {"sorgu": "örnek", "sonuclar": sonuclar},
                        "kaynak": {"belge_parcalari": [s["parca_id"] for s in sonuclar]}}, ensure_ascii=False)
    taban = _istem_token(istemci, [sistem(), {"role": "user", "content": "x"}])
    belgeli = _istem_token(istemci, [sistem(), {"role": "user", "content": yanit}])
    belge_token = belgeli - taban
    return {"model_yukleme_s": yukleme_s, "arac_sayisi": len(araclar),
            "arac_listesi_karakter": len(json.dumps(araclar, ensure_ascii=False)),
            "arac_listesi_token": aracli - bos,
            "ayni_istem_iki_kez_prompt_eval_count": tekrar,
            "en_uzun_belge_sonucu": {"madde": BELGE_EN_FAZLA, "karakter": len(yanit), "token": belge_token,
                                     "karakter_token": round(len(yanit) / belge_token, 2)}}


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sorular", default="", help="virgülle soru numaraları (ör. 9,12); boşsa hepsi")
    ap.add_argument("--not", dest="aciklama", default=None, help="seçili çalıştırmanın rapora yazılacak açıklaması")
    arg = ap.parse_args()
    secili = {int(n) for n in arg.sorular.split(",") if n.strip()}
    rapor_yolu = RAPOR.with_name("faz6_duman_secili.json") if secili else RAPOR
    sys.stdout.reconfigure(encoding="utf-8")
    saat = saat_oku(KOK / "data" / "saat.json")
    anahtar = secrets.token_bytes(32)
    belge = arayici_kur(KOK / "data" / "belge", "anlam")
    olcum = olcumler(belge)
    print(f"araç listesi {olcum['arac_listesi_token']} token; en uzun belge sonucu "
          f"{olcum['en_uzun_belge_sonucu']['token']} token")
    ayar = Ayar()
    satirlar = []
    with tempfile.TemporaryDirectory() as gecici:
        sunucu = SaymanSunucusu(ambar=KOK / "data" / "ambar" / "sayman.duckdb", saat=lambda: saat, anahtar=anahtar,
                                yetki=yetki_oku(), kayit=DenetimKaydi(Path(gecici) / "kayit.jsonl"),
                                kapsam_klasoru=Path(gecici) / "kapsam", belge=belge, belge_yontemi="anlam")
        try:
            asistan = Asistan(sunucu, OllamaModeli(), ayar, kayit=DenetimKaydi(Path(gecici) / "asistan.jsonl"))
            for no, (kullanici, soru) in enumerate(SORULAR, 1):
                if secili and no not in secili:
                    continue
                tur = asistan.sor(soru, kart_uret(kullanici, anahtar, 600))
                ilk = tur.dogrulamalar[0] if tur.dogrulamalar else None
                satirlar.append({
                    "no": no, "kullanici": kullanici, "soru": soru, "durum": tur.durum, "cevap": tur.cevap,
                    "model_cagrisi": tur.model_cagrisi, "sure_s": tur.sure_s,
                    "araclar": [{"arac": c["arac"], "argumanlar": c["argumanlar"],
                                 "hata": c["yanit"].get("hata")} for c in tur.cagrilar],
                    "taslak_sayisi": len(tur.taslaklar),
                    "reddedilen_taslaklar": tur.taslaklar[:-1] if tur.durum == "tamam" else tur.taslaklar,
                    "denetlenen_sayi": len(ilk.sayilar) if ilk else 0,
                    "ilk_taslakta_kaynaksiz": [s.metin for s in ilk.kaynaksiz] if ilk else [],
                    "ilk_taslakta_gecersiz_alinti": [a.neden for a in ilk.gecersiz_alintilar] if ilk else [],
                    "istem_token": [t["istem"] for t in tur.tokenlar],
                    "uretim_token": [t["uretim"] for t in tur.tokenlar],
                    "model_sure_s": [t["sure_s"] for t in tur.tokenlar],
                    "neden": tur.neden})
                print(f"{no:2d} {tur.durum:13s} {tur.sure_s:6.1f} sn  {soru}")
        finally:
            sunucu.kapat()
    taslakli = [s for s in satirlar if s["taslak_sayisi"]]
    ozet = {
        "soru": len(satirlar), "durumlar": dict(Counter(s["durum"] for s in satirlar)),
        "ilk_taslagi_reddedilen": sum(1 for s in taslakli if s["ilk_taslakta_kaynaksiz"]
                                      or s["ilk_taslakta_gecersiz_alinti"]),
        "taslak_yazilan_soru": len(taslakli),
        "ilk_taslaklarda_denetlenen_sayi": sum(s["denetlenen_sayi"] for s in satirlar),
        "ilk_taslaklarda_kaynaksiz_sayi": sum(len(s["ilk_taslakta_kaynaksiz"]) for s in satirlar),
        "gecersiz_alinti_nedenleri": dict(Counter(n for s in satirlar for n in s["ilk_taslakta_gecersiz_alinti"])),
        "sure_s_medyan": round(statistics.median(s["sure_s"] for s in satirlar), 1),
        "sure_s_en_cok": max(s["sure_s"] for s in satirlar),
        "model_cagrisi_ortalama": round(statistics.mean(s["model_cagrisi"] for s in satirlar), 2),
        "istem_token_en_cok": max((t for s in satirlar for t in s["istem_token"] if t), default=None),
    }
    rapor = {"olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "model": MODEL, "secenekler": SECENEKLER, "think": False, "simulasyon_gunu": saat.simdi.isoformat(),
             "belge_yontemi": "anlam",
             "ayar": {k: getattr(ayar, k) for k in ayar.__dataclass_fields__},
             "not": arg.aciklama or NOT, "olcum": olcum, "ozet": ozet, "sorular": satirlar}
    rapor_yolu.write_text(json.dumps(rapor, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(ozet, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
