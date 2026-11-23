"""Phase 5 question set: questions generated from the articles of the synthetic documents, with their correct chunks.

The question set was written before the search code and frozen (its digest is in reports/faz5_soru_seti.json). The only
line added later is the filter that keeps the laws added to the library out of question generation; the set's digest
did not change. The search code (src/sayman/documents) does not read this file.

- Each question asks about a single article; the correct answer is that article's chunk (e.g. "SOZ-S003#m5").
- Contract questions contain the counterparty's name: dozens of contracts have the same article kinds, so the question
  must say which one it asks about. If two companies have the same short name, the full name is used.
- Policy questions contain no counterparty name. Those that could be confused with an article in a customer's contract
  are phrased ("as a general rule", "inside the company") so that only the policy can answer them.
- Each article kind has two patterns: the "close" pattern uses the article's own words, the "far" pattern asks the same
  thing in everyday language, avoiding the article's distinctive words.
- The tuning set (`ayar`) and the held-out set (`ayrilmis`) are separated in two ways: the documents are split in two
  (the same document is never in both sets) and one pattern of a pattern pair goes to the tuning set, the other to the
  held-out set. For a random half of the pattern pairs the close pattern falls in the tuning set, for the other half in
  the held-out set; so the close/far mix of the two sets is similar and the tuning set is not easier than the held-out
  set. Each question's `stil` field says which pattern it came from.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter

from sayman.documents.synthetic import Document, search_text

QUESTIONS_PER_CONTRACT = 4

# field -> (close pattern, far pattern). {taraf}: the counterparty's name.
COMMON = {
    "sure": ("{taraf} ile yaptığımız sözleşme ne zaman başladı, kaç yıl geçerli?",
             "{taraf} anlaşmamızın bitiş tarihi ne, otomatik yenileniyor mu?"),
    "fesih": ("{taraf} sözleşmesini feshetmek için kaç gün önceden yazılı bildirim gerekir?",
              "{taraf} ile işbirliğini erkenden bitirmek istersek ne kadar önceden haber vermemiz gerekir?"),
    "uyusmazlik": ("{taraf} ile uyuşmazlık çıkarsa hangi mahkeme ya da kurum yetkili?",
                   "{taraf} ile aramızda sorun çıkarsa davayı nerede açarız?"),
}
SUPPLY = {
    **COMMON,
    "odeme_vadesi": ("{taraf} faturalarını fatura tarihinden itibaren kaç gün içinde ödemeliyiz?",
                     "{taraf} firmasına olan borcumuzu en geç ne zaman kapatmalıyız?"),
    "erken_odeme": ("{taraf} sözleşmesinde erken ödeme iskontosu var mı?",
                    "{taraf} firmasına borcu günü gelmeden kapatırsak bize bir avantaj sağlıyorlar mı?"),
    "gecikme_faizi": ("{taraf} ödemesini vadesinde yapmazsak ne kadar temerrüt faizi işler?",
                      "{taraf} firmasına geç kalırsak her ay ne kadar ek bedel çıkar?"),
    "teslim": ("{taraf} siparişlerimizi kaç iş günü içinde teslim etmek zorunda?",
               "{taraf} firmasına ısmarladığımız ürünler kaç günde elimize ulaşmalı?"),
    "fiyat": ("{taraf} fiyatlarını hangi durumda güncelleyebilir?",
              "{taraf} zam yapmak isterse buna ne zaman hakkı var?"),
    "ayip": ("{taraf} firmasından gelen ayıplı malı kaç gün içinde bildirmeliyiz?",
             "{taraf} ürünleri bozuk çıkarsa itiraz için ne kadar vaktimiz var?"),
    "cezai_sart": ("{taraf} teslimatı geciktirirse ne kadar cezai şart öder?",
                   "{taraf} malları geç getirirse bize ödeyeceği tazminat nasıl belirleniyor?"),
    "gizlilik": ("{taraf} sözleşmesine göre gizlilik yükümlülüğü kaç yıl sürüyor?",
                 "{taraf} ile aramızdaki ticari sırları anlaşma bittikten sonra ne kadar saklamaları gerekiyor?"),
}
SERVICE = {
    **SUPPLY,
    "teslim": ("{taraf} gönderileri kaç iş gününde alıcısına ulaştırmak zorunda?",
               "{taraf} ile yolladığımız paketler müşteriye kaç günde varıyor?"),
    "kira_artisi": ("{taraf} ile kira sözleşmemizde kira bedeli ne zaman ve ne kadar artırılıyor?",
                    "{taraf} firmasına yerimiz için ödediğimiz para yılda bir hangi orana göre yükseliyor?"),
    "depozito": ("{taraf} firmasına kaç aylık depozito verdik?",
                 "Binadan çıkarken {taraf} bize hangi güvence parasını geri verecek?"),
    "okuma": ("{taraf} sayaçları ayın kaçında okuyor?",
              "{taraf} elektrik tüketimimizi her ay hangi gün ölçüyor?"),
    "kesinti": ("{taraf} planlı kesintiyi kaç saat önce bildirmek zorunda?",
                "{taraf} elektriği bilerek keseceği zaman bizi ne kadar önce uyarmalı?"),
    "hasar_limiti": ("{taraf} taşımada hasar ve kayıp olursa en fazla ne kadar öder?",
                     "{taraf} ile kargoda kırılan paketler için alabileceğimiz tazminatın üst sınırı ne?"),
    "sla": ("{taraf} yazılım hizmeti için hangi erişilebilirlik oranını taahhüt ediyor?",
            "{taraf} sistemi sık sık çökerse faturadan ne kadar düşülür?"),
    "destek": ("{taraf} kritik arızaya kaç saat içinde müdahale etmek zorunda?",
               "{taraf} yazılımı tamamen durursa ekipleri en geç ne zaman devreye girmeli?"),
    "taahhut": ("{taraf} ile taahhüt süremiz kaç ay?",
                "{taraf} hattını erken kapatırsak ne kadar ödememiz gerekir?"),
}
SALES = {
    **COMMON,
    "odeme_vadesi": ("{taraf} faturalarını fatura tarihinden itibaren kaç gün içinde ödüyor?",
                     "{taraf} aldığı malların parasını bize en geç ne zaman yatırmalı?"),
    "teslim_sekli": ("{taraf} ile satışlarda teslim şekli ve para birimi ne?",
                     "{taraf} siparişlerinde nakliye kimin üzerinde, hangi dövizle çalışıyoruz?"),
    "kredi_limiti": ("{taraf} için tanımlı kredi limiti ne kadar?",
                     "{taraf} bize en fazla ne kadar borçlu kalabilir?"),
    "cek": ("{taraf} çekle ödeme yapabilir mi?",
            "{taraf} parayı ileri tarihli bir kâğıtla ödemek isterse kabul ediyor muyuz?"),
    "gecikme_faizi": ("{taraf} vadesinde ödemezse ne kadar temerrüt faizi uygularız?",
                      "{taraf} borcunu gününde kapatmazsa ona her ay ne kadar ek bedel yansıtırız?"),
    "iade": ("{taraf} aldığı ürünleri kaç gün içinde iade edebilir?",
             "{taraf} beğenmediği malları ne kadar süre içinde geri gönderebilir?"),
    "teminat": ("{taraf} bize teminat mektubu vermiş mi, ne kadar?",
                "{taraf} bize karşı bir güvence belgesi vermek zorunda mı, tutarı ne?"),
}
POLICY = {
    "guvenlik_esigi": ("Ana TL hesabının en düşük bakiyesi ne olmalı?",
                       "Şirketin ana banka hesabında sürekli kalması gereken en az para ne kadar?"),
    "banka_dagilimi": ("Toplam nakdin ne kadarı tek bir bankada tutulabilir?",
                       "Paramızı farklı kuruluşlara yaymak zorunda mıyız, birinde en fazla ne kadarı durabilir?"),
    "fazla_nakit": ("Fazla nakit hangi araçlarda ve hangi vadeyle değerlendirilir?",
                    "Kullanmadığımız parayı borsada ya da başka bir yerde nemalandırabilir miyiz?"),
    "nakit_raporu": ("Günlük nakit raporu CFO'ya saat kaçta iletilmeli?",
                     "Her gün paranın durumunu gösteren özet yöneticiye kaça kadar ulaşmalı?"),
    "onay_limitleri": ("Finans müdürü en fazla ne kadarlık ödemeyi onaylayabilir?",
                       "Para çıkışlarına izin verme yetkisi kimde, tutara göre değişiyor mu?"),
    "cift_imza": ("Hangi tutarı aşan ödemeler çift imza gerektirir?",
                  "Çok büyük bir havalede tek yöneticinin izni yetmiyorsa kimler birlikte izin vermeli?"),
    "ilk_odeme": ("Daha önce ödeme yapılmamış bir alıcıya ilk ödemede ne yapılır?",
                  "Hiç çalışmadığımız bir firmaya ilk defa havale yapacaksak önce neyi doğrulamalıyız?"),
    "mukerrer": ("Aynı tedarikçi faturasına ikinci ödeme yapılmasını nasıl engelliyoruz?",
                 "Bir faturanın parası yanlışlıkla iki kere gönderilmek istenirse ne olur?"),
    "takip_adimlari": ("Vadesi geçen alacak için müşteriye ne zaman hatırlatma ve ihtar gönderilir?",
                       "Parasını geciktiren bir alıcıya ilk uyarıyı kaçıncı günde yaparız?"),
    "sevkiyat_durdurma": ("Alacağı geciken müşteriye sevkiyat ne zaman durdurulur?",
                          "Hangi gecikmeden sonra bir alıcının siparişlerini artık yollamayız?"),
    "hukuki_takip": ("Vadesi geçen alacak ne zaman hukuk birimine devredilir?",
                     "Hiç ödemeyen bir müşteriye karşı avukata ne zaman başvururuz?"),
    "limit_asimi": ("Şirket içinde kredi limiti aşımını hangi yönetici onaylar?",
                    "Bir alıcı kendisine tanınan borç tavanını aşmak isterse içeride kimin iznini almalıyız?"),
    "cek_vadesi": ("Genel kural olarak en uzun kaç gün vadeli çek kabul edilir?",
                   "Şirket olarak alabileceğimiz bir çek en fazla ne kadar ileri tarihli olabilir?"),
    "ciro": ("Genel kural olarak ciro edilmiş çek kabul ediyor muyuz?",
             "Kural olarak bir müşteri başkasından aldığı çeki bize verebilir mi?"),
    "tahsile_verme": ("Portföydeki çekler bankaya ne zaman tahsile verilir?",
                      "Müşterilerden aldığımız çekleri paraya çevirmek için ne zaman işleme koyarız?"),
    "karsiliksiz": ("Genel kural olarak çeki karşılıksız çıkan müşteriye nasıl davranılır?",
                    "Kural olarak bir alıcının verdiği çek bankadan dönerse ona sonra nasıl satış yaparız?"),
    "pozisyon_siniri": ("Net açık döviz pozisyonu en fazla ne kadar olabilir?",
                        "Yabancı para borcumuz alacağımızı en fazla ne kadar aşabilir?"),
    "korunma_orani": ("İhracat alacaklarının ne kadarı vadeli döviz satışıyla korunmalı?",
                      "Yurt dışındaki müşterilerden beklediğimiz paraların ne kadarını kur dalgalanmasına karşı "
                      "güvenceye almalıyız?"),
    "araclar": ("Kur riskinden korunmak için hangi araçlara izin veriliyor?",
                "Döviz dalgalanmasına karşı hangi finansal sözleşmeleri yapabiliriz?"),
    "kayit_belgeleri": ("Yeni tedarikçi kaydı için hangi belgeler istenir?",
                        "Bir firmayı satıcı olarak sisteme açmak için ne evrak lazım?"),
    "iban_degisikligi": ("Tedarikçinin banka hesabı değişikliği nasıl sisteme girilir?",
                         "Bir satıcı bize farklı bir IBAN yollarsa parayı hemen oraya gönderebilir miyiz?"),
    "yogunlasma": ("Tek bir tedarikçiden yapılan alımlar en fazla ne kadar olabilir?",
                   "Satın almalarımızı tek bir satıcıya ne kadar bağlayabiliriz?"),
}
PATTERNS = {"tedarik_sozlesmesi": SUPPLY, "hizmet_sozlesmesi": SERVICE, "satis_sozlesmesi": SALES,
            "politika": POLICY}
PAIRS = sorted({c for group in PATTERNS.values() for c in group.values()})


def swapped_pairs(seed: int = 42) -> set[tuple[str, str]]:
    """The pattern pairs whose close pattern goes to the held-out set and far pattern to the tuning set (a random half)."""
    pairs = list(PAIRS)
    random.Random(f"{seed}:belge-kalip").shuffle(pairs)
    return set(pairs[: len(pairs) // 2])


def question_set(documents: list[Document], seed: int = 42) -> dict[str, list[dict]]:
    """{"ayar": [...], "ayrilmis": [...]}: each question is {soru_id, soru, stil, dogru_parca, belge_id, tur, alan,
    deger, roller}."""
    documents = [b for b in documents if b.tur in PATTERNS]  # laws (confusing documents) get no questions
    rng = random.Random(f"{seed}:belge-soru")
    swapped = swapped_pairs(seed)
    short_name_counts = Counter(b.kisa_ad for b in documents if b.kisa_ad)
    tuning_documents = set()
    for kind in sorted({b.tur for b in documents}):
        group = sorted(b.belge_id for b in documents if b.tur == kind)
        rng.shuffle(group)
        tuning_documents.update(group[: (len(group) + 1) // 2])

    result: dict[str, list[dict]] = {"ayar": [], "ayrilmis": []}
    for b in sorted(documents, key=lambda b: b.belge_id):
        articles = [m for m in b.maddeler if m.alan]
        if b.tur != "politika":
            articles = rng.sample(articles, min(QUESTIONS_PER_CONTRACT, len(articles)))
        set_name = "ayar" if b.belge_id in tuning_documents else "ayrilmis"
        counterparty = (b.kisa_ad if short_name_counts[b.kisa_ad] == 1 else b.taraf_ad) if b.kisa_ad else None
        for m in sorted(articles, key=lambda m: m.no):
            pair = PATTERNS[b.tur][m.alan]
            style = "yakin" if (set_name == "ayar") != (pair in swapped) else "uzak"
            result[set_name].append({
                "soru_id": f"{'A' if set_name == 'ayar' else 'H'}-{m.parca_id}",
                "soru": pair[0 if style == "yakin" else 1].format(taraf=counterparty), "stil": style,
                "dogru_parca": m.parca_id, "belge_id": b.belge_id, "tur": b.tur, "alan": m.alan,
                "deger": m.deger, "roller": list(b.roller)})
    return result


def digest(questions: dict[str, list[dict]]) -> str:
    text = json.dumps(questions, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def documents_digest(documents: list[Document]) -> str:
    """Digest over chunk identifier, article text and searched text: it changes if the document generator changes."""
    rows = [[m.parca_id, m.metin, search_text(b, m)] for b in documents for m in b.maddeler]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def chance_baseline(questions: list[dict], documents: list[Document], k: int = 5) -> float | None:
    """Expected hit@k if the search finds the right contract and then orders its articles randomly (contracts only).

    Contract questions contain the counterparty's name, so a word search easily reaches the right document; this baseline
    shows how much of hit@k comes merely from finding the document name."""
    article_counts = {b.belge_id: len(b.maddeler) for b in documents}
    s = [min(k, article_counts[x["belge_id"]]) / article_counts[x["belge_id"]] for x in questions if x["tur"] != "politika"]
    return round(sum(s) / len(s), 4) if s else None
