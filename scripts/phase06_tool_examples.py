"""Phase 6: records real tool results for the verifier's draft tests.

Before the verifier was written, the real tools are called once on the real warehouse and the results are written to
tests/support/phase06_tool_results.json. Draft answers (tests/support/phase06_drafts.py) are written by hand from the
numbers in these results; the tests read this file without opening the warehouse.

Identity cards are signed with a random key specific to this run; the key in .env is not used.

Input:  data/ambar/sayman.duckdb (phase02_build_warehouse.py), data/saat.json
Output: tests/support/phase06_tool_results.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase06_tool_examples.py
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.server import SaymanServer  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

OUTPUT = ROOT / "tests" / "support/phase06_tool_results.json"
CFO, SALES = "selin.aydin", "murat.kaya"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    clock = read_clock(ROOT / "data" / "saat.json")
    key = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory() as temp_dir:
        server = SaymanServer(warehouse=ROOT / "data" / "ambar" / "sayman.duckdb", clock=lambda: clock, key=key,
                                permissions=read_permissions(), audit_log=AuditLog(Path(temp_dir) / "kayit.jsonl"),
                                scope_dir=Path(temp_dir) / "kapsam")
        try:
            def call_tool(user, tool, **arguments):
                response, error = server.gate(tool, arguments, generate_card(user, key))
                return {"kullanici": user, "arac": tool, "argumanlar": arguments, "hata_mi": error,
                        "yanit": response}

            aging = call_tool(CFO, "alacak_yaslandirma", en_fazla=5)
            a, b = (m["musteri"] for m in aging["yanit"]["sonuc"]["en_buyuk_alacaklar"][:2])
            today = clock.simdi
            calls = {
                "yaslandirma": aging,
                "nakit": call_tool(CFO, "nakit_pozisyonu"),
                "musteri_a": call_tool(CFO, "musteri_ozeti", musteri_adi=a),
                "musteri_b": call_tool(CFO, "musteri_ozeti", musteri_adi=b),
                "geciken": call_tool(CFO, "geciken_alacaklar", min_gun=30, en_fazla=5),
                "kur_riski": call_tool(CFO, "kur_riski"),
                "dso": call_tool(CFO, "dso_hesapla", donem="son_90_gun"),
                "yaklasan": call_tool(CFO, "yaklasan_odemeler", gun_sayisi=30, en_fazla=5),
                "cikislar": call_tool(CFO, "banka_hareketleri", baslangic=(today - dt.timedelta(days=7)).isoformat(),
                                  bitis=today.isoformat(), yon="cikis", en_fazla=5),
                "usd": call_tool(CFO, "doviz_kuru", para_birimi="USD"),
                "satis_nakit_ret": call_tool(SALES, "nakit_pozisyonu"),
            }
        finally:
            server.close()
    data = {"aciklama": "Gerçek araçların gerçek ambardaki sonuçları; doğrulayıcı taslak testleri bunlara dayanır.",
            "bugun": clock.simdi.isoformat(), "cagrilar": calls}
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUTPUT.name}: {len(calls)} çağrı, {OUTPUT.stat().st_size:,} bayt")


if __name__ == "__main__":
    main()
