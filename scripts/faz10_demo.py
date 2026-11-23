"""Faz 10 demosu: sahte ERP sayfası ve içine gömülü Sayman sohbet penceresi.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz10_demo.py baslat            # http://127.0.0.1:8010
    .venv\\Scripts\\python -I scripts\\faz10_demo.py baslat --model qwen3.5:9b --belge bm25
    .venv\\Scripts\\python -I scripts\\faz10_demo.py sifirla           # saati ve uyarıları başa alır

- Model varsayılan olarak Faz 8'in ince ayarlı modeli (sayman-ayarli); Ollama açık olmalı (ollama serve).
- Demo durumu data/demo/ klasöründe tutulur (saat kopyası, uyarı deposu, denetim kayıtları). Ambar ve
  data/saat.json değişmez. Saat geri alınamaz; bir provadan sonra "sifirla" ile 1 Eylül 2026'ya dönülür.
- İmza anahtarı önce ortam değişkeninden, yoksa .env'den okunur; tarayıcıya hiç gitmez.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

DURUM = KOK / "data" / "demo"


def baslat(arg) -> int:
    import uvicorn
    from dotenv import dotenv_values

    from sayman.araclar.kimlik import ANAHTAR_DEGISKENI, anahtar_oku
    from sayman.araclar.yetki import yetki_oku
    from sayman.asistan.model import OllamaModeli
    from sayman.belge.arama import arayici_kur
    from sayman.demo.uygulama import Demo, ollama_isitici, uygulama_kur
    from sayman.veri.cikti import csv_oku
    from sayman.veri.saat import saat_oku

    deger = os.environ.get(ANAHTAR_DEGISKENI) or dotenv_values(KOK / ".env").get(ANAHTAR_DEGISKENI) or ""
    anahtar = anahtar_oku({ANAHTAR_DEGISKENI: deger})
    model = OllamaModeli(arg.model, keep_alive="3h")
    belge = None if arg.belge == "yok" else arayici_kur(KOK / "data" / "belge", arg.belge)
    print("Demo kuruluyor (veri okunuyor, kapsam kopyaları kuruluyor)...", flush=True)
    demo = Demo(ambar=KOK / "data" / "ambar" / "sayman.duckdb", durum_klasoru=DURUM,
                baslangic=saat_oku(KOK / "data" / "saat.json"), anahtar=anahtar, yetki=yetki_oku(),
                tablolar=csv_oku(KOK / "data" / "uretilen"), model=model, model_adi=arg.model, belge=belge,
                belge_yontemi="bm25" if arg.belge == "yok" else arg.belge, adim_gun=arg.adim,
                isit=ollama_isitici(model))
    print(f"Hazır: http://127.0.0.1:{arg.port}  (bugün {demo.saat.simdi}; model arka planda ısınıyor)", flush=True)
    try:
        uvicorn.run(uygulama_kur(demo), host="127.0.0.1", port=arg.port, log_level="warning")
    finally:
        demo.kapat()
    return 0


def sifirla() -> int:
    import socket

    with socket.socket() as s:  # demo açıkken dosyaları kilitli: yarım silinmesin
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", 8010)) == 0:
            raise SystemExit("Demo hâlâ çalışıyor (127.0.0.1:8010). Önce kapatın (Ctrl+C), sonra sıfırlayın.")
    hedef = DURUM.resolve()
    if hedef.parent != (KOK / "data").resolve() or hedef.name != "demo":
        raise SystemExit(f"Beklenmeyen klasör: {hedef}")
    if hedef.exists():
        shutil.rmtree(hedef)
        print(f"Silindi: {hedef}. Demo bir sonraki başlatmada data/saat.json'daki günden başlar.")
    else:
        print("Demo durumu zaten yok.")
    return 0


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Sayman Faz 10 demosu")
    alt = ap.add_subparsers(dest="komut", required=True)
    b = alt.add_parser("baslat", help="demoyu başlatır")
    b.add_argument("--model", default="sayman-ayarli", help="Ollama model adı (yedek: qwen3.5:9b)")
    b.add_argument("--belge", choices=("anlam", "bm25", "yok"), default="anlam",
                   help="belge araması: anlam (bge-m3, ölçülen düzen), bm25 (daha az bellek) ya da yok")
    b.add_argument("--port", type=int, default=8010)
    b.add_argument("--adim", type=int, default=7, choices=range(1, 32), metavar="1-31",
                   help="'Zamanı ileri sar' düğmesinin gün sayısı")
    alt.add_parser("sifirla", help="demo durumunu siler (saat başa döner)")
    arg = ap.parse_args()
    return baslat(arg) if arg.komut == "baslat" else sifirla()


if __name__ == "__main__":
    raise SystemExit(main())
