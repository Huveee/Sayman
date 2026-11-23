"""Phase 6: small smoke test with the real model and context measurements (reported separately from the software tests).

1. Measurement: how many tokens the list of 14 tools takes, how many tokens the longest document search result takes,
   and whether Ollama's prompt_eval_count is affected by the cache. To keep the cache from interfering, a random
   prefix is put at the start of every measurement prompt.
2. Smoke test: 13 fixed questions, three roles, the real warehouse and document library (semantic search), the real
   gate. For every question the end state, the displayed answer, the tool calls, the rejected drafts and the token
   counts are written.

Model: qwen3.5:9b, temperature 0, seed 42, thinking off. Identity cards are signed with a random key specific to this
run; the key in .env is not used. Audit records are written to a temporary folder.

Input:  data/ambar/sayman.duckdb, data/saat.json, data/belge/ (belgeler.jsonl and the embedding cache)
Output: reports/faz6_duman.json (if --sorular is given, reports/faz6_duman_secili.json; the main report is unchanged)

Usage (from the project root; Ollama must be running):
    .venv\\Scripts\\python -I scripts\\phase06_smoke.py
    .venv\\Scripts\\python -I scripts\\phase06_smoke.py --sorular 9 --not "neden yeniden soruldu"
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.tools.catalog import DOCUMENT_MAX_LIMIT, DOCUMENT_TEXT_LIMIT, _truncate  # noqa: E402
from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.server import SaymanServer  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.assistant.model import MODEL, OPTIONS, OllamaModel  # noqa: E402
from sayman.assistant.orchestrator import Assistant, Settings, tool_definitions  # noqa: E402
from sayman.documents.search import build_searcher  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

REPORT = ROOT / "reports" / "faz6_duman.json"
NOTE = ("İkinci çalıştırma. İlk çalıştırma (reports/faz6_duman_ilk.json, süre sınırı 120 sn) doğrulayıcıda üç açık "
       "gösterdi (\"TL**\" biçim işareti, \"09-15 Eylül\" tarih aralığı, kısa rakamsız tırnaklı söz); bunlar kapatıldı "
       "ve süre sınırı Faz 0 hızından 300 sn'ye çıkarıldı. Sorular aynıdır: bu bağımsız bir ölçüm değil, duman testidir.")
CFO, DOMESTIC, EXPORT = "selin.aydin", "murat.kaya", "deniz.arslan"
QUESTIONS = [
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
    (DOMESTIC, "Bankada ne kadar paramız var?"),
    (DOMESTIC, "Vadesi geçmiş alacaklarımız ne durumda?"),
    (EXPORT, "En büyük alacaklarımız hangi müşterilerde?"),
]


def _prompt_tokens(client, messages, tools=None) -> int:
    r = client.chat(model=MODEL, messages=messages, tools=tools, think=False, keep_alive="15m",
                     options={**OPTIONS, "num_predict": 1})
    return r.prompt_eval_count


def measure(documents) -> dict:
    import ollama
    client = ollama.Client(timeout=600)

    def system_message():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] Sen bir finans asistanısın."}

    start = time.perf_counter()
    _prompt_tokens(client, [system_message(), {"role": "user", "content": "Merhaba"}])  # so that the model gets loaded
    load_s = round(time.perf_counter() - start, 1)
    tools = tool_definitions()
    bare = _prompt_tokens(client, [system_message(), {"role": "user", "content": "Merhaba"}])
    with_tools = _prompt_tokens(client, [system_message(), {"role": "user", "content": "Merhaba"}], tools)
    same = [system_message(), {"role": "user", "content": "Merhaba"}]
    repeat = [_prompt_tokens(client, same, tools), _prompt_tokens(client, same, tools)]

    # Worst-case document result: the 3 longest articles the CFO can see, truncated to the tool's limit.
    longest = sorted((p for p in documents.chunk_by_id.values() if "cfo" in p.roller), key=lambda p: -len(p.metin))
    results = []
    for p in longest[:DOCUMENT_MAX_LIMIT]:
        text, truncated = _truncate(p.metin, DOCUMENT_TEXT_LIMIT)
        results.append({"parca_id": p.parca_id, "belge": p.belge_baslik, "madde": p.madde_baslik, "metin": text,
                         **({"metin_kesildi": True} if truncated else {})})
    response = json.dumps({"cagri_id": "C000000000000", "arac": "belge_ara", "tarih": "2026-09-01",
                        "kapsam": "tüm şirket", "sonuc": {"sorgu": "örnek", "sonuclar": results},
                        "kaynak": {"belge_parcalari": [s["parca_id"] for s in results]}}, ensure_ascii=False)
    baseline = _prompt_tokens(client, [system_message(), {"role": "user", "content": "x"}])
    with_document = _prompt_tokens(client, [system_message(), {"role": "user", "content": response}])
    document_tokens = with_document - baseline
    return {"model_yukleme_s": load_s, "arac_sayisi": len(tools),
            "arac_listesi_karakter": len(json.dumps(tools, ensure_ascii=False)),
            "arac_listesi_token": with_tools - bare,
            "ayni_istem_iki_kez_prompt_eval_count": repeat,
            "en_uzun_belge_sonucu": {"madde": DOCUMENT_MAX_LIMIT, "karakter": len(response), "token": document_tokens,
                                     "karakter_token": round(len(response) / document_tokens, 2)}}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sorular", default="", help="virgülle soru numaraları (ör. 9,12); boşsa hepsi")
    parser.add_argument("--not", dest="aciklama", default=None, help="seçili çalıştırmanın rapora yazılacak açıklaması")
    args = parser.parse_args()
    selected = {int(n) for n in args.sorular.split(",") if n.strip()}
    report_path = REPORT.with_name("faz6_duman_secili.json") if selected else REPORT
    sys.stdout.reconfigure(encoding="utf-8")
    clock = read_clock(ROOT / "data" / "saat.json")
    key = secrets.token_bytes(32)
    documents = build_searcher(ROOT / "data" / "belge", "anlam")
    measurement = measure(documents)
    print(f"araç listesi {measurement['arac_listesi_token']} token; en uzun belge sonucu "
          f"{measurement['en_uzun_belge_sonucu']['token']} token")
    settings = Settings()
    rows = []
    with tempfile.TemporaryDirectory() as temp_dir:
        server = SaymanServer(warehouse=ROOT / "data" / "ambar" / "sayman.duckdb", clock=lambda: clock, key=key,
                                permissions=read_permissions(), audit_log=AuditLog(Path(temp_dir) / "kayit.jsonl"),
                                scope_dir=Path(temp_dir) / "kapsam", belge=documents, search_method="anlam")
        try:
            assistant = Assistant(server, OllamaModel(), settings, audit_log=AuditLog(Path(temp_dir) / "asistan.jsonl"))
            for number, (user, question) in enumerate(QUESTIONS, 1):
                if selected and number not in selected:
                    continue
                turn = assistant.ask(question, generate_card(user, key, 600))
                first = turn.verifications[0] if turn.verifications else None
                rows.append({
                    "no": number, "kullanici": user, "soru": question, "durum": turn.status, "cevap": turn.answer,
                    "model_cagrisi": turn.model_calls, "sure_s": turn.sure_s,
                    "araclar": [{"arac": c["arac"], "argumanlar": c["argumanlar"],
                                 "hata": c["yanit"].get("hata")} for c in turn.calls],
                    "taslak_sayisi": len(turn.drafts),
                    "reddedilen_taslaklar": turn.drafts[:-1] if turn.status == "tamam" else turn.drafts,
                    "denetlenen_sayi": len(first.numbers) if first else 0,
                    "ilk_taslakta_kaynaksiz": [s.metin for s in first.unsourced] if first else [],
                    "ilk_taslakta_gecersiz_alinti": [a.reason for a in first.invalid_citations] if first else [],
                    "istem_token": [t["istem"] for t in turn.tokens],
                    "uretim_token": [t["uretim"] for t in turn.tokens],
                    "model_sure_s": [t["sure_s"] for t in turn.tokens],
                    "neden": turn.reason})
                print(f"{number:2d} {turn.status:13s} {turn.sure_s:6.1f} sn  {question}")
        finally:
            server.close()
    with_drafts = [s for s in rows if s["taslak_sayisi"]]
    summary = {
        "soru": len(rows), "durumlar": dict(Counter(s["durum"] for s in rows)),
        "ilk_taslagi_reddedilen": sum(1 for s in with_drafts if s["ilk_taslakta_kaynaksiz"]
                                      or s["ilk_taslakta_gecersiz_alinti"]),
        "taslak_yazilan_soru": len(with_drafts),
        "ilk_taslaklarda_denetlenen_sayi": sum(s["denetlenen_sayi"] for s in rows),
        "ilk_taslaklarda_kaynaksiz_sayi": sum(len(s["ilk_taslakta_kaynaksiz"]) for s in rows),
        "gecersiz_alinti_nedenleri": dict(Counter(n for s in rows for n in s["ilk_taslakta_gecersiz_alinti"])),
        "sure_s_medyan": round(statistics.median(s["sure_s"] for s in rows), 1),
        "sure_s_en_cok": max(s["sure_s"] for s in rows),
        "model_cagrisi_ortalama": round(statistics.mean(s["model_cagrisi"] for s in rows), 2),
        "istem_token_en_cok": max((t for s in rows for t in s["istem_token"] if t), default=None),
    }
    report = {"olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "model": MODEL, "secenekler": OPTIONS, "think": False, "simulasyon_gunu": clock.simdi.isoformat(),
             "belge_yontemi": "anlam",
             "ayar": {k: getattr(settings, k) for k in settings.__dataclass_fields__},
             "not": args.aciklama or NOTE, "olcum": measurement, "ozet": summary, "sorular": rows}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
