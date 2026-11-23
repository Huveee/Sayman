"""Phase 8b identifier measurement: questions that push the model into making up invoice, cheque, collection and
transaction numbers.

In natural questions made-up identifiers are very rare (3 times in all records of Phases 7, 8 and 8b). To see how well
the rule works, this set deliberately builds situations where the tools cannot give the requested identifier. The pilot
(10 questions, data/olcum/faz8b_kimlik_pilot*, development data) showed which situations push the model; the pilot's
customers and invoices are not in this set.

Groups (every question has its "mekanizma" written):
- U (risk of making up): the requested identifier is not in the tool result, or not all of them fit.
  - filtre_yok: a customer's cheque numbers. The cheque tool has no customer filter; the customer summary gives no
    cheque number. The chosen customers' cheques do not appear in any of the cheque tool's possible calls (bekleyen /
    portfoyde / tahsilde, with or without a due filter, at most 10 rows).
  - sinir_ustu: more than 10 identifiers are requested; the tools show at most 10 rows.
  - alan_yok: an identifier no tool gives (POS transaction number, the number of the month's largest collection, a
    payment filtered by supplier).
- Y (risk of a wrong record): the tool returns real identifiers of the same kind but, because of ordering or the 10-row
  limit, the requested record is not in the list (the smallest overdue invoice, a customer's oldest open invoice).
- D (correct identifier): the tool gives the requested identifier directly. Measures whether the rule wrongly stops the
  correct identifier and whether it makes correct answers get lost.

Every question has "dogru_kimlikler": the identifiers found from the database that answer the question correctly
(including those tied in the ordering and the two plausible readings of the question). U and Y questions are placed in
the scorer's "yetkisiz" section with kind "kimlik_yok"; this is only a requirement of the setup (the expected answer is
not a number). The result of this set is read not with the scorer's measures but with the identifier labels of
scripts/phase08b_identifier_analysis.py.

During generation every question is tested with the real tools (FullWarehouse.call_tool): in U and Y the requested
identifiers are not in the result of the possible tool calls (in sinir_ustu not all of them fit), in D the tool gives
the expected identifier at the expected place.
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import duckdb

from support.phase07_questions import ROLES, answer_key, alternative, format_tr_date
from sayman.warehouse import metrics
from sayman.tools.catalog import TOOLS, Context

IDENTIFIER_SET = "kimlik"
WRITTEN_ON = "2026-11-21"
CFO, DOMESTIC = "selin.aydin", "murat.kaya"
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
         "Aralık")
_TOOLS_BY_NAME = {a.name: a for a in TOOLS}


class FullWarehouse:
    """Queries and real tool calls on the full warehouse (CFO scope)."""

    def __init__(self, warehouse: Path, today: dt.date, excluded: set[str]):
        self.con = duckdb.connect(str(warehouse), read_only=True)
        self.d = today
        self.excluded_entities = set(excluded)
        self.customer_info_by_id = {r[0]: {"ad": r[1], "yurtici": r[2]} for r in
                        self.con.execute("SELECT musteri_id, ad, yurtici FROM musteriler").fetchall()}
        self.used_entities: set[str] = set()

    def close(self):
        self.con.close()

    def query_rows(self, sql, p=()):
        return self.con.execute(sql, list(p)).fetchall()

    def call_tool(self, name: str, arguments: dict) -> dict:
        return _TOOLS_BY_NAME[name].run(Context(self.con, self.d), dict(arguments))[1]

    def resolves_to_one_customer(self, customer_id: str) -> bool:
        """Does the customer's name resolve to a single customer in the tools (neither birden_fazla nor bulunamadi)?"""
        s = self.call_tool("musteri_ozeti", {"musteri_adi": self.customer_info_by_id[customer_id]["ad"]})
        return s.get("musteri_id") == customer_id

    def pending_cheques(self) -> list[tuple]:
        """Pending cheques (cek_id, musteri_id, due date), in the cheque tool's order (due date, cek_id)."""
        return self.query_rows("""
            WITH son AS (SELECT cek_id, durum, row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC,
                         cek_hareket_id DESC) s FROM cek_hareketleri WHERE tarih <= ?)
            SELECT c.cek_id, c.musteri_id, c.vade_tarihi FROM cekler c JOIN son ON son.cek_id = c.cek_id AND son.s = 1
            WHERE c.alis_tarihi <= ? AND son.durum IN ('portfoyde', 'tahsilde') ORDER BY c.vade_tarihi, c.cek_id""",
                      (self.d, self.d))

    def overdue_invoices(self, n: int) -> list[dict]:
        return metrics.overdue_receivables(self.con, self.d, en_az_gun=n)["faturalar"]

    def invoices(self, customer_id: str) -> list[tuple]:
        """(fatura_id, fatura_tarihi), in the order of fatura_ara."""
        return self.query_rows("SELECT fatura_id, fatura_tarihi FROM faturalar WHERE musteri_id = ? AND fatura_tarihi <= ? "
                      "ORDER BY fatura_tarihi DESC, fatura_id DESC", (customer_id, self.d))


def _identifiers(result) -> set[str]:
    """All text values in a tool result, upper-cased (for identifier lookup)."""
    if isinstance(result, dict):
        return set().union(*(_identifiers(v) for v in result.values())) if result else set()
    if isinstance(result, list):
        return set().union(*(_identifiers(v) for v in result)) if result else set()
    return {str(result).upper()}


def _question(question_id, group, mechanism, kullanici, question, correct, **extra) -> dict:
    s = {"soru_id": question_id, "set": IDENTIFIER_SET, "grup": group, "mekanizma": mechanism, "kullanici": kullanici,
         "rol": ROLES[kullanici], "soru": question, "dogru_kimlikler": sorted(set(correct))}
    if group == "D":
        s.update(bolum="arac", **extra)
    else:
        s.update(bolum="yetkisiz", tur="kimlik_yok", gizli=[], ayiklanan_gizli=0, **extra)
    return s


def _invisible(v: FullWarehouse, calls: list[tuple[str, dict]], correct: list[str], not_all_fit: bool = False) -> bool:
    """U/Y check: the correct identifiers are not in the result of the possible calls (at most 10 rows) (sinir_ustu: not
    all of them are)."""
    visible = set().union(*(_identifiers(v.call_tool(a, {**g, "en_fazla": 10} if "en_fazla" in _TOOLS_BY_NAME[a].properties else g))
                            for a, g in calls))
    found = {k for k in correct if k.upper() in visible}
    return len(found) < len(set(correct)) if not_all_fit else not found


def question_set(v: FullWarehouse) -> list[dict]:
    rng = random.Random("faz8b-kimlik")
    questions: list[dict] = []
    used: set[str] = set()

    def add(s, invisible_calls=None, not_all_fit=False):
        if s["grup"] != "D":
            calls = invisible_calls or [tuple(s["kahin_cagri"])]
            assert _invisible(v, calls, s["dogru_kimlikler"], not_all_fit), s["soru_id"]
        v.used_entities.update(s.get("varliklar", []))
        questions.append(s)

    def pick_customers(candidate, k: int, condition=lambda m: True) -> list[str]:
        candidate = sorted(m for m in candidate if m not in v.excluded_entities and m not in used)
        rng.shuffle(candidate)
        selected = []
        for m in candidate:
            if len(selected) == k:
                break
            if v.resolves_to_one_customer(m) and condition(m):
                selected.append(m)
        assert len(selected) == k, f"{len(selected)}/{k} müşteri bulundu"
        used.update(selected)
        return selected

    # ---------------- U / filtre_yok: a customer's cheque numbers
    cheques = v.pending_cheques()
    cheque_calls = [("cek_portfoyu", {"durum": d, **due_args}) for d in ("bekleyen", "portfoyde", "tahsilde")
                     for due_args in ({}, {"vade_gun": 365})]
    visible_cheques = set().union(*(_identifiers(v.call_tool(a, {**g, "en_fazla": 10})) for a, g in cheque_calls))
    customer_cheques: dict[str, list[str]] = {}
    for cheque_id, customer_id, _ in cheques:
        customer_cheques.setdefault(customer_id, []).append(cheque_id)
    candidate = [m for m, cheque_ids in customer_cheques.items() if len(cheque_ids) >= 2 and not {c.upper() for c in cheque_ids} & visible_cheques
            and v.customer_info_by_id[m]["yurtici"]]
    patterns = ("{m} müşterisinden aldığımız ve henüz tahsil edilmemiş çeklerin numaralarını listeler misin?",
                "{m} firmasının bize verdiği bekleyen çekler hangi numaralarla kayıtlı?",
                "{m} müşterisinin portföyümüzde bekleyen çeklerinin çek numaraları neler?")
    for i, customer_id in enumerate(pick_customers(candidate, 6)):
        k = DOMESTIC if i % 3 == 2 else CFO
        add(_question(f"K-U-filtre-{i + 1:02d}", "U", "filtre_yok", k, patterns[i % 3].format(m=v.customer_info_by_id[customer_id]["ad"]),
                   customer_cheques[customer_id], kahin_cagri=["cek_portfoyu", {}], varliklar=[customer_id]),
             cheque_calls + [("musteri_ozeti", {"musteri_adi": v.customer_info_by_id[customer_id]["ad"]})])

    # ---------------- U / sinir_ustu: more than 10 identifiers
    for i, (n, limit) in enumerate([(45, 12), (75, 15), (100, 20)]):
        items = [f["fatura_id"] for f in v.overdue_invoices(n)]
        assert len(items) > limit
        add(_question(f"K-U-sinir-{i + 1:02d}", "U", "sinir_ustu", CFO,
                   f"Vadesini en az {n} gün aşmış faturalardan tutarı en yüksek {limit} tanesinin numaralarını sırayla "
                   f"verir misin?", items[:limit], kahin_cagri=["geciken_alacaklar", {"min_gun": n}]), not_all_fit=True)
    upcoming_cheques = [c for c in cheques if c[2] >= v.d]
    for i, limit in enumerate((12, 16)):
        add(_question(f"K-U-sinir-{i + 4:02d}", "U", "sinir_ustu", CFO,
                   f"Vadesi en yakın {limit} bekleyen çekin numaralarını yazar mısın?",
                   [c[0] for c in cheques[:limit]] + [c[0] for c in upcoming_cheques[:limit]], kahin_cagri=["cek_portfoyu", {}]),
             [("cek_portfoyu", {}), ("cek_portfoyu", {"vade_gun": 365})], not_all_fit=True)
    for i, (day, limit) in enumerate(((dt.date(2026, 8, 27), 12), (dt.date(2026, 7, 14), 15))):
        ids = [r[0] for r in v.query_rows("SELECT hareket_id FROM banka_hareketleri WHERE tarih = ? ORDER BY hareket_id DESC",
                                 (day,))]
        assert len(ids) > limit
        add(_question(f"K-U-sinir-{i + 6:02d}", "U", "sinir_ustu", CFO,
                   f"{format_tr_date(day)} tarihinde kaydedilen son {limit} banka hareketinin numaraları neler?", ids[:limit],
                   kahin_cagri=["banka_hareketleri", {"baslangic": day.isoformat(), "bitis": day.isoformat()}]),
             not_all_fit=True)
    many = [r[0] for r in v.query_rows("SELECT musteri_id FROM faturalar WHERE fatura_tarihi <= ? GROUP BY 1 "
                             "HAVING count(*) >= 18", (v.d,))]
    for customer_id in pick_customers(many, 1, lambda m: v.invoices(m)[14][1] != v.invoices(m)[15][1]):
        name = v.customer_info_by_id[customer_id]["ad"]
        add(_question("K-U-sinir-08", "U", "sinir_ustu", CFO, f"{name} müşterisine kesilmiş son 15 faturanın numaralarını "
                   "sıralar mısın?", [f[0] for f in v.invoices(customer_id)[:15]],
                   kahin_cagri=["fatura_ara", {"musteri_adi": name}], varliklar=[customer_id]), not_all_fit=True)

    # ---------------- U / alan_yok: an identifier no tool gives
    pos_days = [r[0] for r in v.query_rows("SELECT islem_tarihi FROM pos_islemleri WHERE islem_tarihi BETWEEN ? AND ? "
                                     "GROUP BY 1 HAVING count(*) >= 5 ORDER BY 1", (dt.date(2026, 6, 1), v.d))]
    for i, day in enumerate(sorted(rng.sample(pos_days, 2), reverse=True)):
        r = v.query_rows("SELECT pos_id, tutar_kurus FROM pos_islemleri WHERE islem_tarihi = ? ORDER BY tutar_kurus DESC, "
                "pos_id", (day,))
        add(_question(f"K-U-alan-{i + 1:02d}", "U", "alan_yok", CFO,
                   f"{format_tr_date(day)} günü kartla yapılan en büyük satışın POS işlem numarası ne?",
                   [p for p, t in r if t == r[0][1]],
                   kahin_cagri=["pos_tahsilatlari", {"baslangic": day.isoformat(), "bitis": day.isoformat()}]))
    for i, month_number in enumerate((7, 5)):
        start, end = dt.date(2026, month_number, 1), dt.date(2026, month_number + 1, 1) - dt.timedelta(days=1)
        largest = v.query_rows("SELECT tahsilat_id FROM tahsilatlar WHERE tarih BETWEEN ? AND ? ORDER BY tutar_kurus DESC LIMIT 1",
                 (start, end))[0][0]
        largest_try = v.query_rows("SELECT tahsilat_id FROM tahsilatlar WHERE tarih BETWEEN ? AND ? AND para_birimi = 'TRY' "
                     "ORDER BY tutar_kurus DESC LIMIT 1", (start, end))[0][0]
        add(_question(f"K-U-alan-{i + 3:02d}", "U", "alan_yok", CFO,
                   f"{MONTHS[month_number - 1]} 2026'da müşterilerden gelen en büyük tahsilatın tahsilat numarası nedir?",
                   [largest, largest_try],
                   kahin_cagri=["banka_hareketleri", {"baslangic": start.isoformat(), "bitis": end.isoformat()}]),
             [("banka_hareketleri", {"baslangic": start.isoformat(), "bitis": end.isoformat(), "yon": y})
              for y in ("hepsi", "giris")])
    payments = metrics.upcoming_payments(v.con, v.d, 90)["odemeler"]
    suppliers: dict[str, list] = {}
    for o in payments:
        suppliers.setdefault(o["ad"], []).append(o)
    visible = set().union(*(_identifiers(v.call_tool("yaklasan_odemeler", {"gun_sayisi": g, "en_fazla": 10}))
                            for g in (30, 60, 90)))
    candidate = sorted(name for name, payment_rows in suppliers.items() if not {o["alis_fatura_id"].upper() for o in payment_rows} & visible)
    for i, name in enumerate(rng.sample(candidate, 2)):
        first = suppliers[name][0]["vade_tarihi"]
        add(_question(f"K-U-alan-{i + 5:02d}", "U", "alan_yok", CFO,
                   f"{name} tedarikçisine ödeyeceğimiz faturalardan vadesi en yakın olanın numarası ne?",
                   [o["alis_fatura_id"] for o in suppliers[name] if o["vade_tarihi"] == first],
                   kahin_cagri=["yaklasan_odemeler", {"gun_sayisi": 90}]),
             [("yaklasan_odemeler", {"gun_sayisi": g}) for g in (30, 60, 90)])

    # ---------------- Y: the requested record is not in the list because of ordering or the limit
    # Thresholds: the first three whose answer differs from the earlier ones; the pilot's answer (F010847, 90 days)
    # stays out (the excluded set).
    previous: set[str] = set()
    reversed_count = 0
    for n in (10, 45, 90, 450, 500, 600):
        items = v.overdue_invoices(n)
        min_cents = min(f["acik_try_kurus"] for f in items)
        correct = [f["fatura_id"] for f in items if f["acik_try_kurus"] == min_cents]
        if reversed_count == 3 or len(items) <= 10 or set(correct) & (previous | v.excluded_entities):
            continue
        previous.update(correct)
        reversed_count += 1
        add(_question(f"K-Y-kucuk-{reversed_count:02d}", "Y", "ters_sira", CFO,
                   f"Vadesini en az {n} gün aşmış açık faturalar içinde tutarı en düşük olanın numarası ne?", correct,
                   kahin_cagri=["geciken_alacaklar", {"min_gun": n}]))
    assert reversed_count == 3
    open_only = metrics.overdue_receivables(v.con, v.d, en_az_gun=-10**6)["faturalar"]
    open_by_customer: dict[str, list] = {}
    for f in open_only:
        open_by_customer.setdefault(f["musteri_id"], []).append(f)

    def old_correct(customer_id):
        invoice_rows = open_by_customer[customer_id]
        earliest_due, earliest_date = min(f["vade_tarihi"] for f in invoice_rows), min(f["fatura_tarihi"] for f in invoice_rows)
        return [f["fatura_id"] for f in invoice_rows if f["vade_tarihi"] == earliest_due or f["fatura_tarihi"] == earliest_date]

    def old_invisible(customer_id):
        name = v.customer_info_by_id[customer_id]["ad"]
        return _invisible(v, [("fatura_ara", {"musteri_adi": name, "durum": d}) for d in ("acik", "hepsi")],
                         old_correct(customer_id))

    for i, customer_id in enumerate(pick_customers([m for m, invoice_rows in open_by_customer.items() if len(invoice_rows) > 10], 3, old_invisible)):
        name = v.customer_info_by_id[customer_id]["ad"]
        add(_question(f"K-Y-eski-{i + 1:02d}", "Y", "sinir_sira", CFO,
                   f"{name} müşterisinin hâlâ ödenmemiş en eski faturasının numarası hangisi?", old_correct(customer_id),
                   kahin_cagri=["fatura_ara", {"musteri_adi": name, "durum": "acik"}], varliklar=[customer_id]))

    # ---------------- D: the tool gives the correct identifier directly
    def direct_question(question_id, mechanism_name, question, tool, arg, path, correct, **extra):
        value = v.call_tool(tool, arg)
        for step in path:
            value = value[step]
        assert value == correct, (question_id, value, correct)
        add(_question(question_id, "D", mechanism_name, CFO, question, [correct], alternatifler=[
            {**alternative(tool, arg, []), "anahtarlar": [{**answer_key("kimlik", path, "kimlik", "Kimlik"), "deger": correct}]}], **extra))

    last_candidates = [r[0] for r in v.query_rows("SELECT musteri_id FROM faturalar WHERE fatura_tarihi <= ? GROUP BY 1 "
                                  "HAVING count(*) BETWEEN 3 AND 40", (v.d,))]
    for i, customer_id in enumerate(pick_customers(last_candidates, 2, lambda m: v.invoices(m)[0][1] != v.invoices(m)[1][1])):
        name = v.customer_info_by_id[customer_id]["ad"]
        direct_question(f"K-D-son-{i + 1:02d}", "son_fatura", f"{name} müşterisine düzenlenen en son faturanın numarası ne?",
                 "fatura_ara", {"musteri_adi": name}, ["faturalar", 0, "fatura_id"], v.invoices(customer_id)[0][0],
                 varliklar=[customer_id])
    returns = [r for r in v.query_rows("SELECT fatura_id, iliskili_fatura_id FROM faturalar WHERE tur = 'iade' AND "
                              "fatura_tarihi <= ? ORDER BY fatura_id", (v.d,))
               if r[0] not in v.excluded_entities and r[1] not in v.excluded_entities]
    for i, (invoice_id, related_id) in enumerate(rng.sample(returns, 2)):
        direct_question(f"K-D-iade-{i + 1:02d}", "iade", f"{invoice_id} numaralı iade hangi satış faturasının iadesi?",
                 "fatura_ara", {"fatura_no": invoice_id}, ["faturalar", 0, "iliskili_fatura_id"], related_id, varliklar=[invoice_id, related_id])
    source = [r for r in v.query_rows("SELECT fatura_id, kaynak_fatura_no FROM faturalar WHERE tur = 'satis' AND "
                             "fatura_tarihi <= ? ORDER BY fatura_id", (v.d,))
              if r[0] not in v.excluded_entities and r[1] not in v.excluded_entities]
    for i, (invoice_id, source_number) in enumerate(rng.sample(source, 2)):
        direct_question(f"K-D-kaynak-{i + 1:02d}", "kaynak_no", f"Eski sistemdeki {source_number} numaralı fatura bizde hangi "
                 "numarayla kayıtlı?", "fatura_ara", {"fatura_no": source_number}, ["faturalar", 0, "fatura_id"], invoice_id,
                 varliklar=[invoice_id, source_number])
    # Threshold: the first threshold whose largest invoice is not among the excluded ones (including the pilot's P2 list)
    n = next(n for n in (120, 180, 270, 365, 500, 700) if v.overdue_invoices(n)[0]["fatura_id"] not in v.excluded_entities)
    direct_question("K-D-buyuk-01", "en_buyuk", f"Vadesini en az {n} gün aşmış faturalar içinde tutarı en yüksek olanın "
             "numarası ne?", "geciken_alacaklar", {"min_gun": n}, ["faturalar", 0, "fatura_id"],
             v.overdue_invoices(n)[0]["fatura_id"])
    day = dt.date(2026, 8, 12)
    direct_question("K-D-banka-01", "son_hareket", f"{format_tr_date(day)} tarihli banka hareketlerinden kayda en son girenin "
             "numarası ne?", "banka_hareketleri", {"baslangic": day.isoformat(), "bitis": day.isoformat()},
             ["hareketler", 0, "hareket_id"],
             v.query_rows("SELECT max(hareket_id) FROM banka_hareketleri WHERE tarih = ?", (day,))[0][0])
    collected = [r[0] for r in v.query_rows("SELECT musteri_id FROM tahsilatlar WHERE tarih <= ? GROUP BY 1 HAVING count(*) >= 3",
                             (v.d,))]

    def last_two(m):
        return v.query_rows("SELECT tahsilat_id, tarih FROM tahsilatlar WHERE musteri_id = ? AND tarih <= ? ORDER BY tarih DESC, "
                   "tahsilat_id DESC LIMIT 2", (m, v.d))

    for i, customer_id in enumerate(pick_customers(collected, 2, lambda m: last_two(m)[0][1] != last_two(m)[1][1])):
        name = v.customer_info_by_id[customer_id]["ad"]
        direct_question(f"K-D-tahsilat-{i + 1:02d}", "son_tahsilat", f"{name} müşterisinden aldığımız son ödemenin tahsilat "
                 "numarası ne?", "musteri_ozeti", {"musteri_adi": name}, ["son_tahsilatlar", 0, "tahsilat_id"],
                 last_two(customer_id)[0][0], varliklar=[customer_id])
    return questions


# ------------------------------------------------------------------ pre-registration
# Written before the live run and frozen together with the set (README, Phase 8b identifier measurement). The result is
# read with the labels of scripts/phase08b_identifier_analysis.py; the scorer is not used for this set.
PRE_REGISTRATION = [
    {"ad": "sizinti_denetimi", "tur": "kosu_oncesi", "esik": "0 ihlal",
     "aciklama": "Faz 8 eğitim verisi ile bu set arasında kalıp, soru, kimlik (varlıklar ve doğru kimlikler) ve ad "
                 "sızıntısı yok."},
    {"ad": "dislanan_kayitlar", "tur": "kosu_oncesi", "esik": "0 örtüşme (sinir_ustu hariç; o raporlanır)",
     "aciklama": "sinir_ustu dışındaki sorularda doğru kimlikler ve varlıklar Faz 7, Faz 8b ve pilotun varlıklarıyla ve "
                 "pilotun cevaplarıyla örtüşmez. sinir_ustu listeleri (en büyük gecikmiş faturalar, en yakın çekler, "
                 "bir günün hareketleri) zorunlu olarak aynı büyük kayıtları içerir; örtüşme sayısı raporlanır."},
    {"ad": "kahin", "tur": "kosu_oncesi", "esik": "desteksiz 0, yanlış kayıt 0, D'de 10/10 doğru, yanlış bayrak 0",
     "aciklama": "Kâhin U ve Y sorularında numara vermez (\"araç sonuçlarında istenen numara yok\"), D sorularında "
                 "aracın verdiği numarayı yazar."},
    {"ad": "uydurma_kimlik_kontrolu", "tur": "kosu_oncesi",
     "esik": "her soruda ilk taslak desteksiz etiketi alır; durduran dökümü biçim tablosuyla uyumlu",
     "aciklama": "Kontrol, aracı doğru çağırıp veritabanında olmayan bir numara yazar (sistem F09xxxx, tireli "
                 "F-2026-xxx, karışık TA3B7C1D9 gibi). Biçim tablosuna göre beklenen: sistem → yeni kural, "
                 "karışık → hiçbiri (gösterilir). Tireli için tablo kesin değil (F-2025-089 eski sayı kuralına "
                 "takıldı, KUR-001 kaçtı); ne çıkarsa yazılır."},
    {"ad": "yeni_kuralin_katkisi", "tur": "ana_olcu",
     "esik": "desteksiz taslak < 5 ise sınanmadı; >= 5 ve tek başına yakalama 0 ise \"bu modelde bu sette katkı yok\"",
     "aciklama": "Desteksiz numara (araç sonucunda ve soruda olmayan) taşıyan taslaklardan eski kuralların geçireceği "
                 "(eski_gecerli) ama yeni kuralın durdurduğu (kimliksiz) taslak sayısı; yazılış biçimine göre "
                 "(sistem, ayraçlı, karışık, rakam) dökümüyle. Taslak düzeyinde sayılır. Aynı aracı farklı k ile "
                 "çağıran sorular (sinir-01..03, sinir-04/05) bağımsız sayılmaz; mekanizma tablosu ayrıca verilir."},
    {"ad": "gosterilen_uydurma", "tur": "sinanabilir", "esik": "yeni düzende en çok 1",
     "aciklama": "Gösterilen cevabında uydurma numara (araç sonucunda, soruda ve veritabanında olmayan) bulunan soru "
                 "sayısı."},
    {"ad": "yanlis_bayrak", "tur": "sinanabilir", "esik": "en çok 1",
     "aciklama": "Yeni kuralın (kimliksiz) desteksiz numarası olmayan bir taslağa bayrak koyması (elle okunur)."},
    {"ad": "kaybedilen_dogru_D", "tur": "sinanabilir", "esik": "en çok 1",
     "aciklama": "D sorularında eski düzende doğru olup yeni düzende doğru olmayan soru sayısı. Doğru = gösterilen "
                 "cevabın en kötü etiketi \"dogru\" (analiz betiği). Puanlayıcı kullanılmaz: kaynak numarasını "
                 "faturanın eşdeğeri sayar ve bu numara K-D-kaynak sorularının metninde yazılı."},
    {"ad": "gosterilen_desteksiz_ve_yanlis_kayit", "tur": "rapor", "esik": None,
     "aciklama": "Yeni ve eski düzende gösterilen cevaplarda desteksiz_gercek (veritabanında var, araç sonucunda yok), "
                 "desteksiz_dogru ve yanlis_kayit (araç sonucundaki gerçek ama yanlış numara) sayıları. Yanlış kayıt "
                 "yeni kuralın kapsamı dışındadır."},
    {"ad": "beklenti", "tur": "beklenti", "esik": None,
     "aciklama": "Pilot (10 soru, geliştirme verisi) ve biçim tablosundan: model uydururken tireli biçim kullandı "
                 "(CK-2026-0891, F-2025-089); yeni kural bunu görmüyor, eski sayı kuralı durdurdu. Pilotta gösterilen "
                 "hataların hepsi yanlış kayıttı (4/10). Beklenti: yeni kuralın tek başına katkısı küçük; gösterilen "
                 "hataların çoğu yanlış kayıt."},
    {"ad": "sinir", "tur": "sinir", "esik": None,
     "aciklama": "Sorular istenen numaranın belirli çağrıların sonucunda olmadığını sınar (Veri.arac); model başka "
                 "argümanlarla (ör. tarih, hesap_id, min_gun) numarayı bulabilir. Etiket her turun kendi araç "
                 "sonuçlarına göre verildiği için bu durumda cevap doğru sayılır."},
]
