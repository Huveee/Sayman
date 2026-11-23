"""Phase 10 demo: a fake ERP page with the Sayman chat window embedded in it.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase10_demo.py baslat            # http://127.0.0.1:8010
    .venv\\Scripts\\python -I scripts\\phase10_demo.py baslat --model qwen3.5:9b --belge bm25
    .venv\\Scripts\\python -I scripts\\phase10_demo.py sifirla           # puts the clock and the alerts back to the start

- By default the model is the fine-tuned model of Phase 8 (sayman-ayarli); Ollama must be running (ollama serve).
- The demo state is kept in the data/demo/ folder (clock copy, alert store, audit logs). The warehouse and
  data/saat.json do not change. The clock cannot be turned back; after a rehearsal go back to 1 September 2026 with "sifirla".
- The signing key is read first from the environment variable, otherwise from .env; it never goes to the browser.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STATE_DIR = ROOT / "data" / "demo"
DOCUMENT_PDFS = ROOT / "data" / "belge" / "pdf"


def start_demo(arg) -> int:
    import uvicorn
    from dotenv import dotenv_values

    from sayman.tools.identity import KEY_ENV_VAR, read_key
    from sayman.tools.permissions import read_permissions
    from sayman.assistant.model import OllamaModel
    from sayman.documents.search import build_searcher
    from sayman.demo.app import Demo, ollama_warmer, build_app
    from sayman.data.table_io import read_csv
    from sayman.data.clock import read_clock

    value = os.environ.get(KEY_ENV_VAR) or dotenv_values(ROOT / ".env").get(KEY_ENV_VAR) or ""
    signing_key = read_key({KEY_ENV_VAR: value})
    model = OllamaModel(arg.model, keep_alive="3h")
    document_search = None if arg.belge == "yok" else build_searcher(ROOT / "data" / "belge", arg.belge)
    print("Demo kuruluyor (veri okunuyor, kapsam kopyaları kuruluyor)...", flush=True)
    demo = Demo(warehouse=ROOT / "data" / "ambar" / "sayman.duckdb", state_dir=STATE_DIR,
                initial_clock=read_clock(ROOT / "data" / "saat.json"), key=signing_key, permissions=read_permissions(),
                tables=read_csv(ROOT / "data" / "uretilen"), model=model, model_name=arg.model, belge=document_search,
                search_method="bm25" if arg.belge == "yok" else arg.belge, step_days=arg.adim,
                warmer=ollama_warmer(model), document_pdfs=DOCUMENT_PDFS)
    if document_search is not None and not demo.article_pages:
        print("Uyarı: belge PDF'leri yok; cevaptaki kaynak bağlantıları açılmaz. Üretmek için: "
              ".venv\\Scripts\\python -I scripts\\phase10_document_pdfs.py", flush=True)
    print(f"Hazır: http://127.0.0.1:{arg.port}  (bugün {demo.clock.simdi}; model arka planda ısınıyor)", flush=True)
    try:
        uvicorn.run(build_app(demo), host="127.0.0.1", port=arg.port, log_level="warning")
    finally:
        demo.close()
    return 0


def reset_demo() -> int:
    import socket

    with socket.socket() as s:  # while the demo is open its files are locked: do not delete halfway
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", 8010)) == 0:
            raise SystemExit("Demo hâlâ çalışıyor (127.0.0.1:8010). Önce kapatın (Ctrl+C), sonra sıfırlayın.")
    target = STATE_DIR.resolve()
    if target.parent != (ROOT / "data").resolve() or target.name != "demo":
        raise SystemExit(f"Beklenmeyen klasör: {target}")
    if target.exists():
        shutil.rmtree(target)
        print(f"Silindi: {target}. Demo bir sonraki başlatmada data/saat.json'daki günden başlar.")
    else:
        print("Demo durumu zaten yok.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Sayman Faz 10 demosu")
    subparsers = ap.add_subparsers(dest="komut", required=True)
    b = subparsers.add_parser("baslat", help="demoyu başlatır")
    b.add_argument("--model", default="sayman-ayarli", help="Ollama model adı (yedek: qwen3.5:9b)")
    b.add_argument("--belge", choices=("anlam", "bm25", "yok"), default="anlam",
                   help="belge araması: anlam (bge-m3, ölçülen düzen), bm25 (daha az bellek) ya da yok")
    b.add_argument("--port", type=int, default=8010)
    b.add_argument("--adim", type=int, default=7, choices=range(1, 32), metavar="1-31",
                   help="'Zamanı ileri sar' düğmesinin gün sayısı")
    subparsers.add_parser("sifirla", help="demo durumunu siler (saat başa döner)")
    arg = ap.parse_args()
    return start_demo(arg) if arg.komut == "baslat" else reset_demo()


if __name__ == "__main__":
    raise SystemExit(main())
