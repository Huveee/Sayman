"""Kaggle deneme eğitimi (duman testi) için az sayıda gerçek biçimli örnek üretir.

Örnekler geliştirme setinden, kâhin modelle gerçek orkestratörden geçirilerek üretilir: her model çağrısı bir örnektir
(istem = Ollama'nın o çağrıda çizdiği metin, tamamlama = kâhinin cevabı). Bu örnekler yalnızca düzeneği sınar
(bellek, hız, kayıp, dışa aktarma); asıl eğitimde kullanılmaz ve bu eğitimle elde edilen model ölçülmez.
Geliştirme seti değerlendirme için ayrılmış kalır.

Çıktı: data/egitim/duman/ornekler.jsonl
Kullanım (proje kökünden, Ollama açıkken):
    .venv\\Scripts\\python -I scripts\\faz8_duman_ornek.py
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.araclar.kayit import DenetimKaydi  # noqa: E402
from sayman.araclar.kimlik import kart_uret  # noqa: E402
from sayman.araclar.sunucu import SaymanSunucusu  # noqa: E402
from sayman.araclar.yetki import yetki_oku  # noqa: E402
from sayman.asistan.orkestrator import Asistan, Ayar  # noqa: E402
from sayman.belge import kutuphane  # noqa: E402
from sayman.egitim.istem import KayitciModel, istem_ciz, tamamlama  # noqa: E402
from sayman.olcum.kahin import Kahin, KahinArayici  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

CIKTI = KOK / "data" / "egitim" / "duman" / "ornekler.jsonl"
SORULAR = ["G-nakit_toplam-01", "G-yas_toplam-01", "G-gec_ikinci-01", "G-dso_donem-01", "G-kur_bugun-01",
           "G-oz_acik_fatura-01", "G-fat_bilgi-01", "G-akis_ilk_hafta-01", "G-belge-01", "G-belge-02", "G-belge-03",
           "G-yetkisiz-baska-01", "G-yetkisiz-yasak-01"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    veri = json.loads((KOK / "reports" / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    gelistirme = [s for s in veri["sorular"] if s["set"] == "gelistirme"]
    secili = [s for s in gelistirme if s["soru_id"] in SORULAR]
    if len(secili) != len(SORULAR):
        raise SystemExit("geliştirme setinde olmayan soru kimliği")
    parcalar = {p.parca_id: p for p in kutuphane.parcalar(kutuphane.oku(KOK / "data" / "belge" / "belgeler.jsonl"))}
    model = KayitciModel(Kahin(None, parcalar))
    arayici = KahinArayici(parcalar)
    saat = saat_oku(KOK / "data" / "saat.json")
    anahtar = secrets.token_bytes(32)
    ornekler = []
    with tempfile.TemporaryDirectory() as gecici:
        sunucu = SaymanSunucusu(ambar=KOK / "data" / "ambar" / "sayman.duckdb", saat=lambda: saat, anahtar=anahtar,
                                yetki=yetki_oku(), kayit=DenetimKaydi(Path(gecici) / "denetim.jsonl"),
                                kapsam_klasoru=Path(gecici) / "kapsam", belge=arayici, belge_yontemi="anlam")
        try:
            asistan = Asistan(sunucu, model, Ayar())
            for s in secili:
                model.soru = arayici.soru = s
                tur = asistan.sor(s["soru"], kart_uret(s["kullanici"], anahtar, 900))
                for adim, c in enumerate(model.bosalt(), 1):
                    ornekler.append({"soru_id": s["soru_id"], "adim": adim, "durum": tur.durum,
                                     "istem": istem_ciz(c["mesajlar"], c["araclar"]),
                                     "tamamlama": tamamlama(c["cevap"])})
                print(s["soru_id"], tur.durum, tur.model_cagrisi, flush=True)
        finally:
            sunucu.kapat()
    # Kontrol: ilk istemi Ollama'ya ham (raw) verip temel modelin kendiliğinden ne yazdığını ve istemin Ollama'da kaç
    # token tuttuğunu kaydet. Kaggle'daki HF token sayısı bununla karşılaştırılır (önbellek isabeti sayıyı düşürebilir).
    import httpx

    from sayman.asistan.model import MODEL, SECENEKLER

    r = httpx.post("http://127.0.0.1:11434/api/generate", timeout=600, json={
        "model": MODEL, "prompt": ornekler[0]["istem"], "raw": True, "stream": False,
        "options": {**SECENEKLER, "num_predict": 200}})
    r.raise_for_status()
    ham = r.json()
    ornekler[0]["ollama_istem_token"] = ham.get("prompt_eval_count")
    CIKTI.parent.mkdir(parents=True, exist_ok=True)
    (CIKTI.parent / "ham_uretim.json").write_text(json.dumps(
        {"soru_id": ornekler[0]["soru_id"], "prompt_eval_count": ham.get("prompt_eval_count"),
         "cevap": ham.get("response"), "beklenen": ornekler[0]["tamamlama"]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    with open(CIKTI, "w", encoding="utf-8", newline="\n") as f:
        for o in ornekler:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(len(ornekler), "örnek;", "en uzun istem", max(len(o["istem"]) for o in ornekler), "karakter")
    print("ham üretim:", repr(ham.get("response", "")[:300]), "| istem tokeni:", ham.get("prompt_eval_count"))


if __name__ == "__main__":
    main()
