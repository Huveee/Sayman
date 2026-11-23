"""Phase 3: tries the tools on the real data and reports.

- 60 unauthorized attempts (tests/support/authorization_trials.py), from an MCP client session; leakage count.
- In group C, how many attempts really touched data that is held back (did the CFO get a different answer to the
  same question).
- Size of the tool list; size and duration of each tool's longest answer (`en_fazla`=10, widest range).
- --token: if Ollama is up, how many tokens the model chosen in Phase 0 actually counts these texts as
  (prompt_eval_count, minus the count of a short message).

Inputs: data/ambar/sayman.duckdb (phase02_build_warehouse.py), data/uretilen, data/saat.json
Outputs: reports/faz3_araclar.json; data/denetim/faz3_denemeler.jsonl (rewritten from scratch on every run)
Identity cards are signed with a random key specific to this run; the key in .env is not used.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase03_tools_report.py [--token]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import support.authorization_trials as fd  # noqa: E402

from sayman.tools.audit_log import AuditLog, verify_chain  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.server import SaymanServer  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.data.table_io import SCHEMA, read_csv, data_digest  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
GENERATED = ROOT / "data" / "uretilen"
AUDIT_LOG = ROOT / "data" / "denetim" / "faz3_denemeler.jsonl"
PHASE0_REPORT = ROOT / "reports" / "faz0_hiz.json"
NUM_CTX = 8192


def ollama_tools(server) -> list[dict]:
    """The form the model sees (Ollama): name, description, parameter schema."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.inputSchema}} for t in server.tools()]


class TokenCounter:
    def __init__(self, model: str):
        import ollama

        self.client, self.model = ollama.Client(), model
        self.baseline = self._count_tokens("a")

    def _count_tokens(self, text: str, tools=None) -> int:
        r = self.client.chat(model=self.model, messages=[{"role": "user", "content": text}], tools=tools,
                              think=False, keep_alive="10m",
                              options={"num_ctx": NUM_CTX, "num_predict": 1, "temperature": 0, "seed": 42})
        return r.prompt_eval_count

    def metin(self, text: str) -> int:
        return self._count_tokens(text) - self.baseline + 1  # "a" is a single token

    def count_tool_list(self, tools: list[dict]) -> int:
        return self._count_tokens("a", tools) - self.baseline


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", action="store_true", help="Ollama ile gerçek token sayısını ölç")
    a = parser.parse_args()

    clock = read_clock(ROOT / "data" / "saat.json")
    T = read_csv(GENERATED)
    key, epoch = secrets.token_bytes(32), time.time()
    AUDIT_LOG.unlink(missing_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="sayman_faz3_"))

    start = time.perf_counter()
    server = SaymanServer(WAREHOUSE, lambda: clock, key, read_permissions(), AuditLog(AUDIT_LOG),
                            scope_dir=ROOT / "data" / "ambar" / "kapsam", time_fn=lambda: epoch)
    build_s = round(time.perf_counter() - start, 2)
    judge = fd.Judge(T, clock.simdi, temp_dir)
    try:
        # ---- 60 attempts
        trials = fd.trials(T, clock.simdi, clock.veri_bitis)
        start = time.perf_counter()
        results = fd.run_trials(server, trials, key, epoch, judge)
        trials_s = round(time.perf_counter() - start, 2)
        groups = {g: {"adet": sum(r["grup"] == g for r in results),
                       "sorunsuz": sum(r["grup"] == g and not r["sorunlar"] for r in results)} for g in "ABCDE"}
        group_c = [r for r in results if r["grup"] == "C"]

        # ---- scope warehouses
        scope_row_counts = {k: {t: server.connections[k].execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                for t in ("musteriler", "faturalar", "tahsilatlar", "cekler")}
                            for k in sorted(server.connections)}
        empty_tables = {k: sorted(t for t in SCHEMA if not server.connections[k].execute(
            f"SELECT count(*) FROM {t}").fetchone()[0]) for k in fd.SCOPES}

        # ---- longest answers
        top_customer = server.connections["hepsi"].execute(
            "SELECT m.ad FROM faturalar f JOIN musteriler m USING (musteri_id) GROUP BY m.ad "
            "ORDER BY count(*) DESC, m.ad LIMIT 1").fetchone()[0]
        answers = []
        for user in fd.USERS.values():
            role = server.permissions.roller[server.permissions.users[user]]
            card = generate_card(user, key, 3600, epoch)
            for tool, args in fd.widest_calls(clock.simdi, top_customer):
                if tool not in role.tools:
                    continue
                start = time.perf_counter()
                response, error = fd.call_mcp(server, [(tool, args, True, card)])[0]
                duration = round(time.perf_counter() - start, 3)
                if error:
                    raise RuntimeError(f"{user} {tool}: {response}")
                answers.append({"arac": tool, "en_fazla": args.get("en_fazla"), "kullanici": user,
                                 "karakter": len(json.dumps(response, ensure_ascii=False)), "sure_s": duration,
                                 "_metin": json.dumps(response, ensure_ascii=False)})
        longest = {}
        for c in answers:
            longest_key = (c["arac"], c["en_fazla"])
            if longest_key not in longest or c["karakter"] > longest[longest_key]["karakter"]:
                longest[longest_key] = c
        longest = sorted(longest.values(), key=lambda c: -c["karakter"])

        # ---- tool list and tokens
        tool_list_json = json.dumps(ollama_tools(server), ensure_ascii=False)
        phase0 = json.loads(PHASE0_REPORT.read_text(encoding="utf-8"))
        candidate0 = next(x for x in phase0["adaylar"] if x["model"] == phase0["secilen_model"])
        token_estimate = round(len(tool_list_json) * candidate0["arac_blogu_token_yaklasik"] / 6698)
        measured = None
        output = ROOT / "reports" / "faz3_araclar.json"
        if a.token:
            counter = TokenCounter(phase0["secilen_model"])
            measured = {"model": phase0["secilen_model"], "olcum_zamani": dt.datetime.now().astimezone().isoformat(
                timespec="seconds"), "arac_listesi": counter.count_tool_list(ollama_tools(server))}
            for c in longest:
                c["token"] = counter.metin(c["_metin"])
        elif output.exists():
            # On a run without Ollama the previous measurement is kept (if the answer length did not change, the token count is too).
            previous = json.loads(output.read_text(encoding="utf-8"))
            measured = previous.get("token_olcumu")
            if measured:
                measured.setdefault("olcum_zamani", previous.get("olcum_zamani"))
            old = {(c["arac"], c["en_fazla"], c["kullanici"], c["karakter"]): c.get("token")
                    for c in previous.get("en_uzun_cevaplar", [])}
            for c in longest:
                if old.get((c["arac"], c["en_fazla"], c["kullanici"], c["karakter"])) is not None:
                    c["token"] = old[(c["arac"], c["en_fazla"], c["kullanici"], c["karakter"])]
        if measured and all("token" in c for c in longest):
            # Window budget: the tool list, system prompt + question (measured in Phase 0), room for writing the answer (512) and
            # one tool call per turn (~34 tokens in Phase 0) + that call's answer.
            largest = max(c["token"] for c in longest)
            turn = max(t["uretim_token"] for t in candidate0["turlar"])
            remaining = NUM_CTX - measured["arac_listesi"] - candidate0["aracsiz_istem_token"] - 512
            measured["en_buyuk_cevap"] = largest
            measured["baglam"] = {"num_ctx": NUM_CTX, "arac_listesi": measured["arac_listesi"],
                                 "sistem_ve_soru": candidate0["aracsiz_istem_token"], "cevap_payi": 512,
                                 "arac_cagrisi_turu": turn, "kalan": remaining,
                                 "en_buyuk_cevaptan_sigan": remaining // (largest + turn),
                                 "sonrasinda_bos_kalan": remaining % (largest + turn)}
        for c in longest:
            c.pop("_metin")
        slowest = max(answers, key=lambda c: c["sure_s"])

        audit_records = AuditLog.read(AUDIT_LOG)
        gate_slowest = max(audit_records, key=lambda r: r["sure_ms"])
        report = {
            "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "veri_hash": data_digest(T)[0], "simdi": clock.simdi.isoformat(), "veri_bitis": clock.veri_bitis.isoformat(),
            "sunucu_kurulum_s": build_s,
            "araclar": [t.name for t in server.tools()],
            "roller": {k: {"ad": r.name, "kapsam": r.scope, "araclar": sorted(r.tools),
                           "kullanicilar": sorted(u for u, rr in server.permissions.users.items() if rr == k)}
                       for k, r in server.permissions.roller.items()},
            "kapsam_satirlari": scope_row_counts, "satis_kapsaminda_bos_tablolar": empty_tables,
            "arac_listesi": {"karakter": len(tool_list_json), "token_tahmini_faz0_oraniyla": token_estimate,
                             "token_olculen": measured["arac_listesi"] if measured else None},
            "denemeler": {
                "toplam": len(results), "sorunsuz": sum(not r["sorunlar"] for r in results),
                "sizinti": sum(any("kapsam dışı" in s or "farklı" in s for s in r["sorunlar"]) for r in results),
                "gruplar": groups, "sure_s": trials_s,
                "c_grubu_gizledi": f"{sum(r['gizledi'] for r in group_c)}/{len(group_c)}",
                "c_grubu_gizlemeyen": [{"kullanici": r["kullanici"], "arac": r["arac"]} for r in group_c
                                       if not r["gizledi"]],
                "sonuclar": results,
            },
            "denetim_kaydi": {"dosya": str(AUDIT_LOG.relative_to(ROOT)).replace("\\", "/"), "satir": len(audit_records),
                              "zincir_hatalari": verify_chain(audit_records)},
            "en_uzun_cevaplar": longest,
            "sureler": {"cagri_sayisi": len(audit_records),
                        "kapida_en_yavas_ms": gate_slowest["sure_ms"], "kapida_en_yavas_arac": gate_slowest["arac"],
                        "istemciden_en_yavas_s": slowest["sure_s"], "istemciden_en_yavas_arac": slowest["arac"]},
            "token_olcumu": measured,
        }
    finally:
        judge.close()
        server.close()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    d = report["denemeler"]
    print(f"Sunucu kuruldu ({build_s} s); {len(report['araclar'])} araç, 3 rol. Bugün: {clock.simdi}")
    for k, s in scope_row_counts.items():
        print(f"  {k}: {s['musteriler']} müşteri, {s['faturalar']} fatura, {s['cekler']} çek")
    print(f"60 deneme: {d['sorunsuz']}/{d['toplam']} beklendiği gibi, sızıntı {d['sizinti']} "
          f"(C grubunda gerçekten saklanan veri: {d['c_grubu_gizledi']}) - {trials_s} s")
    for g, x in groups.items():
        print(f"  {g}: {x['sorunsuz']}/{x['adet']}")
    for r in results:
        if r["sorunlar"]:
            print(f"  SORUN {r['no']} {r['arac']} {r['argumanlar']}: {r['sorunlar']}")
    print(f"Denetim kaydı: {len(audit_records)} satır, zincir {'sağlam' if not report['denetim_kaydi']['zincir_hatalari'] else 'BOZUK'}")
    tok = f", ölçülen {measured['arac_listesi']} token" if measured else ""
    print(f"Araç listesi: {len(tool_list_json)} karakter (Faz 0 oranıyla ~{token_estimate} token{tok})")
    for c in longest[:6]:
        tk = f", {c['token']} token" if "token" in c else ""
        print(f"  {c['arac']} (en_fazla={c['en_fazla']}, {c['kullanici']}): {c['karakter']} karakter{tk}, {c['sure_s']} s")
    print(f"En yavaş çağrı: kapıda {gate_slowest['sure_ms']} ms ({gate_slowest['arac']}), istemciden "
          f"{slowest['sure_s']} s ({slowest['arac']}); {len(audit_records)} çağrı")
    if measured and "baglam" in measured:
        b = measured["baglam"]
        source = "ölçüldü" if a.token else f"önceki ölçüm, {measured.get('olcum_zamani', '?')}"
        print(f"Bağlam ({source}): {NUM_CTX} - araç listesi {b['arac_listesi']} - sistem ve soru {b['sistem_ve_soru']} - "
              f"cevap payı 512 = {b['kalan']} token; en büyük cevaptan (+ çağrı turu) {b['en_buyuk_cevaptan_sigan']} "
              f"tane sığar, sonra {b['sonrasinda_bos_kalan']} token boş kalır")
    print(f"Yazıldı: {output.relative_to(ROOT)}")
    return 0 if d["sorunsuz"] == d["toplam"] and not report["denetim_kaydi"]["zincir_hatalari"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
