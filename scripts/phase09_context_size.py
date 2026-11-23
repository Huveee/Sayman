"""Faz 9 ön ölçümü: iki araç listesi ve otomatik araçların sayfaları modelin bağlamına (8192 token) ne kadar yer kaplar?

Ölçüm Ollama'nın prompt_eval_count değeriyle yapılır (faz6_duman.py ile aynı yöntem): aynı istem araçlı ve araçsız
sorulur, fark araç listesinin token sayısıdır. Ön belleğin karışmaması için her istemin başına rastgele bir etiket
konur. Sayfalar için CFO'nun faturalar, tahsilatlar ve çekler listelerinin 10 ve 25 kayıtlık sayfaları bir araç
mesajı olarak verilir. Tahmin için karakter/token oranı da yazılır (orkestratör tahmini 1,7 kullanır).

Model: temel qwen3.5:9b (Faz 9'da iki kol da bununla ölçülür). Ollama'da başka iş yokken çalıştırın; önce
scripts/faz8_ollama_model.py bosalt.

Çıktı: reports/faz9_boyut.json

    .venv\\Scripts\\python -I -u scripts\\faz9_boyut.py
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.tools.audit_log import DenetimKaydi  # noqa: E402
from sayman.tools.identity import kart_uret  # noqa: E402
from sayman.tools.permissions import yetki_oku  # noqa: E402
from sayman.assistant.model import MODEL, SECENEKLER  # noqa: E402
from sayman.assistant.orchestrator import SISTEM_ISTEMI, Ayar, arac_tanimlari  # noqa: E402
from sayman.experiment.generated_tools import OtomatikSunucu  # noqa: E402
from sayman.data.clock import saat_oku  # noqa: E402

AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
LISTELER = ("faturalar_api_v1_faturalar_get", "tahsilatlar_api_v1_tahsilatlar_get", "cekler_api_v1_cekler_get")


def main() -> None:
    import ollama
    sys.stdout.reconfigure(encoding="utf-8")
    istemci = ollama.Client(timeout=900)

    def token(mesajlar, araclar=None) -> int:
        r = istemci.chat(model=MODEL, messages=mesajlar, tools=araclar, think=False, keep_alive="15m",
                         options={**SECENEKLER, "num_predict": 1})
        return r.prompt_eval_count

    def kisa():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] Sen bir finans asistanısın."}

    def tam():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] {SISTEM_ISTEMI}"}

    saat = saat_oku(KOK / "data" / "saat.json")
    anahtar = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory() as g:
        s = OtomatikSunucu(AMBAR, lambda: saat, anahtar, yetki_oku(), DenetimKaydi(Path(g) / "d.jsonl"), Path(g) / "k")
        try:
            kart = kart_uret("selin.aydin", anahtar, 900)
            sayfalar = {}
            for ad in LISTELER:
                for boyut in (10, 25):
                    yanit, hata = s.kapi(ad, {"sayfa_boyutu": boyut}, kart)
                    assert not hata, yanit
                    sayfalar[f"{ad}:{boyut}"] = json.dumps(yanit, ensure_ascii=False)
            el, oto = arac_tanimlari(), s.arac_tanimlari()
        finally:
            s.kapat()

    bas = time.perf_counter()
    token([kisa(), {"role": "user", "content": "Merhaba"}])  # model yüklensin
    yukleme_s = round(time.perf_counter() - bas, 1)
    bos = token([kisa(), {"role": "user", "content": "Merhaba"}])
    el_t = token([kisa(), {"role": "user", "content": "Merhaba"}], el) - bos
    oto_t = token([kisa(), {"role": "user", "content": "Merhaba"}], oto) - bos
    sistem_t = token([tam(), {"role": "user", "content": "Merhaba"}]) - bos
    taban = token([kisa(), {"role": "user", "content": "x"}])
    sayfa = {}
    for anahtar_ad, metin in sayfalar.items():
        t = token([kisa(), {"role": "user", "content": metin}]) - taban
        sayfa[anahtar_ad] = {"karakter": len(metin), "token": t, "karakter_token": round(len(metin) / t, 2)}
        print(anahtar_ad, sayfa[anahtar_ad], flush=True)

    ayar = Ayar()
    butce = ayar.baglam_token - ayar.cevap_payi
    sonuc = {
        "model": MODEL, "num_ctx": ayar.baglam_token, "cevap_payi": ayar.cevap_payi, "butce": butce,
        "model_yukleme_s": yukleme_s,
        "sistem_istemi_token": sistem_t,
        "el": {"arac_sayisi": len(el), "karakter": len(json.dumps(el, ensure_ascii=False)), "token": el_t,
               "kalan": butce - sistem_t - el_t},
        "otomatik": {"arac_sayisi": len(oto), "karakter": len(json.dumps(oto, ensure_ascii=False)), "token": oto_t,
                     "kalan": butce - sistem_t - oto_t},
        "sayfalar": sayfa,
        "not": "kalan: soru, model mesajları ve araç sonuçları için kalan token (bütçe - sistem istemi - araç listesi)",
    }
    (KOK / "reports" / "faz9_boyut.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n",
                                                    encoding="utf-8")
    print(json.dumps({k: v for k, v in sonuc.items() if k != "sayfalar"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
