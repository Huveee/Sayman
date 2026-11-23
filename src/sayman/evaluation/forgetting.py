"""Faz 8 unutma testi: ince ayar modelin genel Türkçesini bozdu mu?

Belebele'nin Türkçe dosyasından (okuma-anlama, 4 şık, CC BY-SA 4.0) sabit bir alt küme seçilir. Her soruda model bir
metni okur ve doğru şıkkın numarasını yazar. Temel model ve ince ayarlı model aynı sorularla, aynı Ollama
seçenekleriyle (sıcaklık 0, tohum 42, düşünme kapalı) ve araçsız, sistem istemsiz ölçülür.

Hedef (ince ayardan önce yazıldı): ince ayarlı modelin doğruluğu temel modelinkinden en çok 5 puan düşük olabilir.
200 soruda tek bir oranın standart hatası yaklaşık 3 puandır; bu yüzden fark ayrıca eşleştirilmiş olarak (aynı
soruda biri doğru, öbürü yanlış) raporlanır.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from sayman.evaluation.pairing import mcnemar_p

SORU_SAYISI = 200
TOHUM = 20261120
EN_COK_DUSUS = 5.0       # puan (yüzde puanı)
SECENEKLER = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 8}

_RAKAM = re.compile(r"(?<!\d)([1-4])(?!\d)")


def sorulari_oku(yol: Path) -> list[dict]:
    satirlar = [json.loads(s) for s in Path(yol).read_text(encoding="utf-8").splitlines() if s.strip()]
    sorular = []
    for s in satirlar:
        sorular.append({"soru_id": f"{s['link']}#{s['question_number']}", "metin": s["flores_passage"],
                        "soru": s["question"], "siklar": [s[f"mc_answer{i}"] for i in range(1, 5)],
                        "dogru": int(s["correct_answer_num"])})
    return sorted(sorular, key=lambda s: s["soru_id"])


def alt_kume(sorular: list[dict], n: int = SORU_SAYISI, tohum: int = TOHUM) -> list[dict]:
    """Soru kimliğine göre sıralı listeden tohumla seçilen sabit alt küme (sırası da sabit)."""
    return random.Random(tohum).sample(sorular, n)


def istem(soru: dict) -> str:
    siklar = "\n".join(f"{i}) {s}" for i, s in enumerate(soru["siklar"], 1))
    return (f"Aşağıdaki metni oku ve soruyu cevapla.\n\nMetin: {soru['metin']}\n\nSoru: {soru['soru']}\n\n{siklar}\n\n"
            "Yalnızca doğru seçeneğin numarasını yaz (1, 2, 3 ya da 4).")


def cevap_numarasi(metin: str) -> int | None:
    """Cevaptaki ilk tek başına duran 1-4 rakamı; yoksa None (yanlış sayılır)."""
    m = _RAKAM.search(metin or "")
    return int(m.group(1)) if m else None


def ozetle(kayitlar: list[dict]) -> dict:
    n = len(kayitlar)
    dogru = sum(1 for k in kayitlar if k["secilen"] == k["dogru"])
    return {"soru": n, "dogru": dogru, "dogruluk": round(100 * dogru / n, 1) if n else None,
            "okunamayan": sum(1 for k in kayitlar if k["secilen"] is None)}


def karsilastir(temel: list[dict], ayarli: list[dict]) -> dict:
    """Aynı sorularda iki modelin karşılaştırması ve hedefin sonucu."""
    t = {k["soru_id"]: k["secilen"] == k["dogru"] for k in temel}
    a = {k["soru_id"]: k["secilen"] == k["dogru"] for k in ayarli}
    ortak = sorted(set(t) & set(a))
    if not ortak:
        raise ValueError("ortak soru yok")
    td = 100 * sum(t[s] for s in ortak) / len(ortak)
    ad = 100 * sum(a[s] for s in ortak) / len(ortak)
    yalniz_temel = sum(1 for s in ortak if t[s] and not a[s])
    yalniz_ayarli = sum(1 for s in ortak if a[s] and not t[s])
    return {"soru": len(ortak), "temel_dogruluk": round(td, 1), "ayarli_dogruluk": round(ad, 1),
            "fark": round(ad - td, 1), "yalniz_temel_dogru": yalniz_temel, "yalniz_ayarli_dogru": yalniz_ayarli,
            "p": mcnemar_p(yalniz_ayarli, yalniz_temel),
            "esik": -EN_COK_DUSUS, "gecti": ad - td >= -EN_COK_DUSUS}
