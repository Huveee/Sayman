"""Phase 9: classifies the 86 tool questions of the Phase 8b "yeni" set against the generated tools (fake API) and freezes the classes.

The criterion is "does the value of the answer appear verbatim in a reachable API response?", not the number of calls:
the system prompt forbids the model's own summing or ratio computation, and the verifier also stops every number that is not
in a tool result (even if it is correct). Classes:
- tek_cagri: the value is verbatim in the result of a single call (an argument may require a date computation: "at least
  120 days overdue" -> vade_bitis = today - 120).
- zincir: the value is verbatim but several calls are needed (name -> identifier -> card) or two results must be compared
  (the first due of the cheques in the portfolio and in collection).
- toplama: the value is in no response; one has to sum or average the records, convert with a rate or scan all the pages
  (there is no sorting by amount).
- yok: the required definition or method is not in the API (cash flow forecast, fx position) or the API's value differs from
  the definition of the question set.
- engelli: the value is verbatim and the plan works correctly, but the oracle's answer does not pass the real verifier (e.g.
  the verifier's customer ownership rule depends on the field names of the hand-written tools). The field names are
  therefore not changed; the result is reported.

For every direct (tek_cagri, zincir) question the plan is run through the real gate (the generated-tools server) with the
question's user; the value the API gives is compared with the expected value in the question set. Then the automatic oracle
(sayman.experiment.oracle) is asked with the real orchestrator and verifier; if the scorer does not count the answer correct
the question becomes "engelli".

Output: reports/faz9_siniflar.json (with their digests: question set, API definition, plans). It is produced once before the measurement.

    .venv\\Scripts\\python -I -u scripts\\phase09_classify.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import sys
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.assistant.orchestrator import Assistant, Settings  # noqa: E402
from sayman.experiment.oracle import DIRECT, GeneratedOracle, resolve  # noqa: E402
from sayman.experiment.generated_tools import GeneratedServer  # noqa: E402
from sayman.evaluation.scoring import alternative_occurs, add_equivalent_identifiers, source_invoice_numbers, get_path  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz8b_soru_seti.json"
OUTPUT = ROOT / "reports" / "faz9_siniflar.json"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
# The generated tool list was measured at 2209 tokens in Ollama (reports/faz9_boyut.json); to the 2336 measured for the
# hand-written tools, the margin given by the default 2400 was added. Used only in the estimate for the first model call.
AUTO_TOOL_LIST_TOKENS = 2300

INVOICES, INVOICE, COLLECTIONS, CHEQUES = ("faturalar_api_v1_faturalar_get", "fatura_api_v1_faturalar", "tahsilatlar_api_v1_tahsilatlar_get",
                 "cekler_api_v1_cekler_get")
CUSTOMERS_LIST, CUSTOMER_CARD, RATES = "musteriler_api_v1_musteriler_get", "musteri_api_v1_musteriler", "kurlar_api_v1_kurlar_get"
ACCOUNTS, TRANSACTIONS = "hesaplar_api_v1_banka_hesaplar_get", "hareketler_api_v1_banka_hesaplar"
PENDING = ("portfoyde", "tahsilde")

AGGREGATION = {
    "oz_gecikme": "Ortalama gecikme için kapanan her faturanın gecikme gününü bulup ortalamak gerekir.",
    "yas_toplam": "Kapsamdaki bütün açık faturaların kalanlarını TL'ye çevirip toplamak gerekir (binlerce kayıt).",
    "cek_bekleyen": "Sayı iki durumun (portföyde, tahsilde) toplamı; tutar bütün bekleyen çeklerin toplamı.",
    "dso": "DSO alacak ve satış toplamlarından hesaplanır.",
    "dso_tarihli": "DSO alacak ve satış toplamlarından hesaplanır.",
    "gec_en_buyuk": "Tutara göre sıralama yok; en büyüğü bulmak için bütün sayfaları taramak gerekir.",
    "nakit_toplam": "Hesap bakiyelerini kurla TL'ye çevirip toplamak gerekir.",
    "pos_komisyon": "Dönemdeki işlemlerin komisyonlarını toplamak gerekir.",
    "ode_toplam": "Vadesi yaklaşan alış faturalarının kalanlarını toplamak gerekir.",
    "yas_dilim": "Dilimdeki bütün açık faturaların kalanlarını TL'ye çevirip toplamak gerekir.",
}
UNSUPPORTED = {
    "akis_ilk_hafta": "Nakit akışı tahmininin yöntemi (hangi alacağın, çekin, borcun hangi hafta sayılacağı) API'de yok.",
    "kur_poz": "Döviz pozisyonunun tanımı (hangi varlık ve borç kalemlerinin netleşeceği) API'de yok.",
}


def plan_step(tool: str, **arguments) -> dict:
    return {"arac": tool, "argumanlar": arguments}


def answer_path(anahtar: int, call_index: int, *path) -> dict:
    return {"anahtar": anahtar, "cagri": call_index, "yol": list(path)}


def values_equal(api, expected) -> bool:
    try:
        return Decimal(str(api)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return str(api) == str(expected)


def band_limits(name: str) -> tuple[int, int | None]:
    """"gecikme_61_90" -> (61, 90); "gecikme_180_ustu" -> (181, None)."""
    p = name.split("_")
    return (int(p[1]) + 1, None) if p[2] == "ustu" else (int(p[1]), int(p[2]))


class Planner:
    def __init__(self, server: GeneratedServer, anahtar: bytes, today: dt.date):
        self.server, self.key, self.today = server, anahtar, today

    def run(self, s: dict, plan: list[dict]) -> list[dict]:
        """Runs the plan with the question's user; the "sonuc" of every step (RuntimeError on an error)."""
        card = generate_card(s["kullanici"], self.key, 900)
        results = []
        for a in plan:
            response, error = self.server.gate(a["arac"], resolve(a["argumanlar"], results), card)
            if error:
                raise RuntimeError(f"{s['soru_id']}: {a['arac']} -> {response}")
            results.append(response)
        return results

    def gun(self, n: int) -> str:
        return (self.today - dt.timedelta(days=n)).isoformat()

    def plan(self, s: dict) -> dict:
        n, first_alternative = s["sablon"].removeprefix("y_"), s["alternatifler"][0]
        x = first_alternative["argumanlar"].get("musteri_adi")
        if n in AGGREGATION:
            return {"sinif": "toplama", "gerekce": AGGREGATION[n]}
        if n in UNSUPPORTED:
            return {"sinif": "yok", "gerekce": UNSUPPORTED[n]}
        if n == "oz_son_tahsilat":
            return {"sinif": "tek_cagri", "plan": [plan_step(COLLECTIONS, musteri_adi=x, sayfa_boyutu=1)],
                    "cevap": [answer_path(0, 0, "kayitlar", 0, "tarih"), answer_path(1, 0, "kayitlar", 0, "tutar")]}
        if n == "oz_eski_vade":
            return {"sinif": "tek_cagri",
                    "plan": [plan_step(INVOICES, musteri_adi=x, durum="acik", siralama="vade_artan", sayfa_boyutu=1)],
                    "cevap": [answer_path(0, 0, "kayitlar", 0, "vade_tarihi")]}
        if n == "oz_acik":
            return {"sinif": "zincir", "gerekce": "Müşteri adından kimliğe, kimlikten müşteri kartına (cari bakiye).",
                    "plan": [plan_step(CUSTOMERS_LIST, ad=x, sayfa_boyutu=1),
                             plan_step(CUSTOMER_CARD, musteri_id={"$": [0, "kayitlar", 0, "musteri_id"]})],
                    "cevap": [answer_path(0, 1, "cari_bakiye_tl")]}
        if n == "gec_adet":
            return {"sinif": "tek_cagri", "gerekce": "Sayı toplam_kayit alanında; vade_bitis = bugün - gün.",
                    "plan": [plan_step(INVOICES, durum="acik", vade_bitis=self.gun(first_alternative["argumanlar"]["min_gun"]), sayfa_boyutu=1)],
                    "cevap": [answer_path(0, 0, "toplam_kayit")]}
        if n == "fat_son":
            return {"sinif": "tek_cagri", "plan": [plan_step(INVOICES, musteri_adi=x, sayfa_boyutu=1)],
                    "cevap": [answer_path(0, 0, "kayitlar", 0, "fatura_id")]}
        if n == "kur_bugun":
            return {"sinif": "tek_cagri", "plan": [plan_step(RATES, para_birimi=first_alternative["argumanlar"]["para_birimi"])],
                    "cevap": [answer_path(0, 0, "alis")]}
        if n == "fat_tutar":
            return {"sinif": "tek_cagri", "plan": [plan_step(INVOICE, fatura_id=first_alternative["argumanlar"]["fatura_no"])],
                    "cevap": [answer_path(0, 0, "tutar")]}
        if n == "cek_en_erken_no":
            plan = [plan_step(CHEQUES, durum="portfoyde", sayfa_boyutu=1), plan_step(CHEQUES, durum="tahsilde", sayfa_boyutu=1)]
            earliest = [(r["sonuc"]["kayitlar"][0]["vade_tarihi"], i) for i, r in enumerate(self.run(s, plan))
                   if r["sonuc"]["kayitlar"]]
            i = min(earliest)[1]
            return {"sinif": "zincir", "gerekce": "Portföydeki ve tahsildeki çeklerin ilk vadelisi karşılaştırılır.",
                    "plan": plan, "cevap": [answer_path(0, i, "kayitlar", 0, "cek_id"), answer_path(1, i, "kayitlar", 0, "musteri")]}
        if n == "oz_cek_tutar":
            plan = [plan_step(CHEQUES, musteri_adi=x)]
            result = self.run(s, plan)[0]["sonuc"]
            pending = [i for i, r in enumerate(result["kayitlar"]) if r["durum"] in PENDING]
            if result["toplam_kayit"] <= len(result["kayitlar"]):
                if len(pending) == 1:
                    return {"sinif": "tek_cagri", "gerekce": "Müşterinin bekleyen tek bir çeki var.", "plan": plan,
                            "cevap": [answer_path(0, 0, "kayitlar", pending[0], "tutar")]}
                return {"sinif": "toplama", "gerekce": f"Bekleyen {len(pending)} çek var; tutarları toplamak gerekir."}
            # The cheques do not fit on one page: the pending ones are found in two calls with the status filter.
            plan = [plan_step(CHEQUES, musteri_adi=x, durum=d) for d in PENDING]
            counts = [r["sonuc"]["toplam_kayit"] for r in self.run(s, plan)]
            if sum(counts) == 1:
                return {"sinif": "zincir", "gerekce": "Bekleyen tek çek, durum süzgeciyle iki çağrıda bulunur.",
                        "plan": plan, "cevap": [answer_path(0, counts.index(1), "kayitlar", 0, "tutar")]}
            return {"sinif": "toplama", "gerekce": f"Müşterinin {result['toplam_kayit']} çeki var, {sum(counts)} tanesi "
                                                   "bekliyor; tutarları toplamak gerekir."}
        if n == "oz_dilim":
            a, b = band_limits(first_alternative["anahtarlar"][0]["yol"][-1])
            arg = {"musteri_adi": x, "durum": "acik", "vade_bitis": self.gun(a)}
            if b is not None:
                arg["vade_baslangic"] = self.gun(b)
            plan = [plan_step(INVOICES, **arg)]
            result = self.run(s, plan)[0]["sonuc"]
            if result["toplam_kayit"] != 1:
                return {"sinif": "toplama", "gerekce": f"Dilimde {result['toplam_kayit']} fatura var; kalanlarını "
                                                       "toplamak gerekir."}
            r = result["kayitlar"][0]
            if r["para_birimi"] == "TRY":
                return {"sinif": "tek_cagri", "gerekce": "Dilimde tek bir TL faturası var (kalan tutar).", "plan": plan,
                        "cevap": [answer_path(0, 0, "kayitlar", 0, "kalan_tutar")]}
            if r["kalan_tutar"] == r["tutar"]:
                return {"sinif": "tek_cagri", "gerekce": "Dilimde tek bir döviz faturası var, hiç ödenmemiş (TL karşılığı).",
                        "plan": plan, "cevap": [answer_path(0, 0, "kayitlar", 0, "tutar_tl")]}
            return {"sinif": "toplama", "gerekce": "Dilimdeki tek döviz faturası kısmen ödenmiş; kalanın TL karşılığı "
                                                   "için kur çarpımı gerekir."}
        if n == "banka_adet":
            arg = dict(first_alternative["argumanlar"])
            accounts = self.run(s, [plan_step(ACCOUNTS)])[0]["sonuc"]["hesaplar"]
            plan = [plan_step(ACCOUNTS)] + [plan_step(TRANSACTIONS, hesap_id={"$": [0, "hesaplar", i, "hesap_id"]}, sayfa_boyutu=1, **arg)
                                    for i in range(len(accounts))]
            counts = [r["sonuc"]["toplam_kayit"] for r in self.run(s, plan)[1:]]
            nonempty = [i for i, k in enumerate(counts) if k]
            if len(nonempty) == 1:
                return {"sinif": "zincir", "gerekce": "Yalnızca bir hesapta hareket var.", "plan": plan,
                        "cevap": [answer_path(0, 1 + nonempty[0], "toplam_kayit")]}
            return {"sinif": "toplama", "gerekce": f"{len(nonempty)} hesapta hareket var ({counts}); sayıları toplamak "
                                                   "gerekir."}
        raise ValueError(f"sınıflandırılmamış niyet: {n}")


def digest(x) -> str:
    return hashlib.sha256(json.dumps(x, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    questions = [s for s in data["sorular"] if s["set"] == "yeni" and s["bolum"] == "arac"]
    clock = read_clock(ROOT / "data" / "saat.json")
    today = clock.simdi
    anahtar = secrets.token_bytes(32)
    records = []
    with tempfile.TemporaryDirectory() as g:
        server = GeneratedServer(WAREHOUSE, lambda: clock, anahtar, read_permissions(), AuditLog(Path(g) / "d.jsonl"),
                                Path(g) / "kapsam")
        try:
            p = Planner(server, anahtar, today)
            plans = {}
            for s in questions:
                k = {"soru_id": s["soru_id"], "niyet": s["sablon"].removeprefix("y_"), "rol": s["rol"], "alternatif": 0, **p.plan(s)}
                if k["sinif"] in DIRECT:
                    results = p.run(s, k["plan"])
                    # The alternative that gives the expected value (there can be several correct answers, such as cheques with equal rank).
                    api = [get_path(results[c["cagri"]]["sonuc"], c["yol"]) for c in k["cevap"]]
                    matching = [i for i, alternative in enumerate(s["alternatifler"])
                            if len(alternative["anahtarlar"]) >= len(k["cevap"])
                            and all(values_equal(v, alternative["anahtarlar"][c["anahtar"]]["deger"]) for v, c in zip(api, k["cevap"]))]
                    k["api_degerleri"] = api
                    if matching:
                        k["alternatif"] = matching[0]
                    else:
                        k.update(sinif="yok", gerekce="API değeri soru setindeki tanımdan farklı: " + ", ".join(
                            f"{v} ≠ {s['alternatifler'][0]['anahtarlar'][c['anahtar']]['deger']}"
                            for v, c in zip(api, k["cevap"])), eski_sinif=k["sinif"])
                plans[s["soru_id"]] = k
                records.append(k)

            # The oracle with the real orchestrator and verifier: "engelli" if the scorer does not count it correct.
            oracle = GeneratedOracle(plans)
            assistant = Assistant(server, oracle, Settings(arac_listesi_token=AUTO_TOOL_LIST_TOKENS))
            assistant.tools = server.tool_definitions()
            for s in questions:
                k = plans[s["soru_id"]]
                if k["sinif"] not in DIRECT:
                    continue
                oracle.soru = s
                turn = assistant.ask(s["soru"], generate_card(s["kullanici"], anahtar, 900))
                successful = [{"hata": c["yanit"].get("hata"), "yanit": c["yanit"]} for c in turn.calls
                            if not c["hata_mi"]]
                accepted_ids = add_equivalent_identifiers(s["alternatifler"], source_invoice_numbers(successful))
                correct = turn.status == "tamam" and alternative_occurs(turn.answer, accepted_ids, today.year)
                k["kahin"] = {"durum": turn.status, "dogru": correct, "cevap": turn.answer,
                              "dogrulama": [{"gecerli": d.gecerli, "kaynaksiz": [x.metin for x in d.unsourced],
                                             "kimliksiz": d.unknown_identifiers, "sahipsiz": d.ownerless} for d in turn.verifications]}
                if not correct:
                    k.update(sinif="engelli", eski_sinif=k["sinif"],
                             gerekce=(k.get("gerekce", "") + " Kâhinin cevabı doğrulayıcıdan geçmedi.").strip())
        finally:
            server.close()
        api_digest = server.digest()

    counts = Counter(k["sinif"] for k in records)
    result = {
        "aciklama": "Faz 9 sınıflandırması: değer ulaşılabilir bir API cevabında aynen geçiyor mu (scripts/phase09_classify.py).",
        "bugun": today.isoformat(), "soru_seti": QUESTION_SET.name, "soru_seti_ozeti": data["ozet"],
        "api_ozeti": api_digest, "otomatik_liste_token": AUTO_TOOL_LIST_TOKENS,
        "sayilar": dict(sorted(counts.items())),
        "niyetler": {n: dict(Counter(k["sinif"] for k in records if k["niyet"] == n))
                     for n in sorted({k["niyet"] for k in records})},
        "sorular": records,
    }
    result["ozet"] = digest(result["sorular"])
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("sayilar", "niyetler", "api_ozeti", "ozet")}, ensure_ascii=False, indent=1))
    for k in records:
        if k["sinif"] in ("engelli", "yok") and "eski_sinif" in k:
            print(k["soru_id"], k["sinif"], k["gerekce"], json.dumps(k.get("kahin"), ensure_ascii=False)[:400])


if __name__ == "__main__":
    main()
