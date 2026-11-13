"""Faz 4 ölçümü: izleme kurallarını olaylı ve olaysız veride çalıştırır, puanlar ve raporlar.

- Olaylı veride: cevap anahtarındaki olayların kaçı, kaç gün içinde yakalandı (tests/faz4_puanlama.py).
- Olaysız ikizde: ayda kaç yanlış alarm (yalnızca önemi "uyari" olanlar sayılır).
Kurallar cevap anahtarını okumaz; anahtarı yalnızca bu betik puanlama için okur.

Tohum 42 kuralların ayarlandığı veridir. Tohum 43 ve 44 ayrılmış veridir: kurallar dondurulduktan sonra
--ayrilmis ile çalıştırılır; sonucuna bakılıp kural değiştirilmez. --ayrilmis verilmezse rapordaki önceki
ayrılmış sonuçlar korunur.

Girdiler: data/uretilen, data/anahtar/olaylar.json, data/izleme_veri (scripts/faz4_veri_uret.py), data/saat.json
Çıktı:    reports/faz4_izleme.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz4_izleme_olc.py
    .venv\\Scripts\\python -I scripts\\faz4_izleme_olc.py --ayrilmis
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

import faz4_puanlama as puan  # noqa: E402

from sayman.izleme.kurallar import Ayarlar  # noqa: E402
from sayman.izleme.zamanlayici import baslangic_gunu, izle  # noqa: E402
from sayman.veri.cikti import csv_oku, veri_hash  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

RAPOR = KOK / "reports" / "faz4_izleme.json"
AYAR_SETLERI = [("tohum42", "tohum42_temiz")]
AYRILMIS_SETLER = [("tohum43", "tohum43_temiz"), ("tohum44", "tohum44_temiz")]


def _ozet(uyarilar: list[dict]) -> str:
    return hashlib.sha256(json.dumps(uyarilar, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _calistir(klasor: Path, bitis: dt.date) -> tuple[dict, list[dict], dict]:
    t = csv_oku(klasor)
    bas = time.perf_counter()
    uyarilar = izle(t, bitis)
    sure = time.perf_counter() - bas
    return t, uyarilar, {"klasor": str(klasor.relative_to(KOK)).replace("\\", "/"), "veri_hash": veri_hash(t)[0],
                         "baslangic": baslangic_gunu(t).isoformat(), "sure_s": round(sure, 2),
                         "uyari": len(uyarilar), "uyari_kurala_gore": dict(sorted(Counter(
                             f"{u['kural']}/{u['onem']}" for u in uyarilar).items())),
                         "uyari_ozeti": _ozet(uyarilar)}


def _cek_paylari(t: dict, uyarilar: list[dict], olaylar: list[dict]) -> list[dict]:
    """Karşılıksız çek olaylarında çek tutarının kuralın eşiğine (son 12 ayın çeklerinin üst çeyreği) payı.
    Üretici bütün dönemin üst çeyreğini kullanır; kural yalnızca geçmişi görür. Pay küçükse ayrılmış veride
    olay "bilgi"ye düşebilir; bu yüzden her olay için yazılır."""
    tutarlar = sorted(c["tutar_kurus"] for c in t["cekler"])
    butun_p75 = tutarlar[len(tutarlar) * 3 // 4]
    cek_uyarilari = {u["varlik"]["id"]: u for u in uyarilar if u["kural"] == "karsiliksiz_cek"}
    sonuc = []
    for o in olaylar:
        if o["tur"] != "karsiliksiz_cek":
            continue
        u = cek_uyarilari.get(o["cek_id"])
        esik = u["esik"]["ust_ceyrek_tl"] if u else None
        tutar = o["tutar_kurus"] / 100
        sonuc.append({"olay_id": o["olay_id"], "cek_id": o["cek_id"], "tutar_tl": f"{tutar:.2f}",
                      "kural_esigi_tl": esik, "butun_donem_esigi_tl": f"{butun_p75 / 100:.2f}",
                      "pay": f"{tutar / float(esik):.3f}" if esik else None, "onem": u["onem"] if u else None})
    return sonuc


def _etkilesimler(olaylar: list[dict]) -> list[dict]:
    """Bir nakit sıkışması olayı, başka bir olayın ödemesini öne çektiyse: o olayın verideki günü anahtardaki
    günden farklıdır. Puan değiştirilmez; yalnızca bir olayın neden kaçmış göründüğünü açıklamak için yazılır."""
    cekilen = {p["odeme_id"]: (o["olay_id"], p["eski_tarih"], o["tarih"])
               for o in olaylar if o["tur"] == "nakit_sikismasi" for p in o["odemeler"]}
    sonuc = []
    for o in olaylar:
        for alan in ("mukerrer_odeme_id", "asil_odeme_id", "odeme_id"):
            if o.get(alan) in cekilen:
                nakit, eski, yeni = cekilen[o[alan]]
                sonuc.append({"olay_id": o["olay_id"], "tur": o["tur"], "odeme_id": o[alan], "cekan_olay": nakit,
                              "anahtardaki_gun": eski, "verideki_gun": yeni})
    return sonuc


def _yanlis_alarm_dokumu(uyarilar: list[dict], profiller: dict) -> list[dict]:
    satirlar = []
    for u in puan.alarmlar(uyarilar):
        satir = {"gun": u["gun"], "kural": u["kural"], "varlik": u["varlik"]["id"]}
        if u["kural"] == "musteri_gecikmesi":
            p = profiller.get(u["varlik"]["id"], {})
            satir["musteri_profili"] = p.get("profil")
            satir["odemeyi_kesti"] = p.get("batik_baslangic")
        satirlar.append(satir)
    return satirlar


def _ornekler(uyarilar: list[dict], olay_uyari_idleri: set[str]) -> dict:
    """Her kuraldan bir örnek (önce bir olayı yakalayan uyarı)."""
    sonuc = {}
    for u in sorted(uyarilar, key=lambda u: (u["uyari_id"] not in olay_uyari_idleri, u["gun"])):
        if u["onem"] == "uyari" and u["kural"] not in sonuc:
            sonuc[u["kural"]] = {k: u[k] for k in ("uyari_id", "kural", "gun", "onem", "baslik", "aciklama", "olcum",
                                                   "esik")}
    return dict(sorted(sonuc.items()))


def olc(olayli: str, temiz: str, setler: dict, bitis: dt.date, ayrilmis: bool) -> dict:
    so, st = setler[olayli], setler[temiz]
    anahtar_yolu = KOK / "data" / "anahtar" / "olaylar.json" if olayli == "tohum42" else KOK / so["klasor"] / "olaylar.json"
    anahtar = json.loads(anahtar_yolu.read_text(encoding="utf-8"))
    t, u_olay, b_olay = _calistir(KOK / so["klasor"], bitis)
    if b_olay["veri_hash"] != so["veri_hash"]:
        raise SystemExit(f"{olayli}: veri özeti setler.json ile aynı değil; önce scripts/faz4_veri_uret.py")
    p = puan.puanla(u_olay, anahtar["olaylar"], bitis)
    t2, u_temiz, b_temiz = _calistir(KOK / st["klasor"], bitis)
    y = puan.yanlis_alarm(u_temiz, baslangic_gunu(t2), bitis)
    gecikmeler = sorted(r["gun_sonra"] for r in p["olaylar"] if r["yakalandi"])
    return {
        "tohum": so["tohum"], "ayrilmis": ayrilmis,
        "olayli": {**b_olay, "puan": p, "cek_olaylari": _cek_paylari(t, u_olay, anahtar["olaylar"]),
                   "olay_etkilesimleri": _etkilesimler(anahtar["olaylar"]),
                   "yakalama_gecikmesi_gun": {"en_az": gecikmeler[0], "ortanca": gecikmeler[len(gecikmeler) // 2],
                                              "en_cok": gecikmeler[-1]} if gecikmeler else None},
        "temiz": {**b_temiz, "yanlis_alarm": y,
                  "karsiliksiz_cek_hareketi": sum(h["durum"] == "karsiliksiz" for h in t2["cek_hareketleri"]),
                  "alarmlar": _yanlis_alarm_dokumu(u_temiz, anahtar.get("musteri_profilleri", {}))},
        "ornek_uyarilar": _ornekler(u_olay, {r["uyari_id"] for r in p["olaylar"] if r["uyari_id"]}) if not ayrilmis else None,
    }


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--ayrilmis", action="store_true", help="tohum 43 ve 44'ü de çalıştır (kurallar donduktan sonra)")
    arg = ap.parse_args()
    setler = json.loads((KOK / "data" / "izleme_veri" / "setler.json").read_text(encoding="utf-8"))
    bitis = saat_oku(KOK / "data" / "saat.json").veri_bitis
    onceki = json.loads(RAPOR.read_text(encoding="utf-8")) if RAPOR.exists() else {}

    sonuc = {}
    for olayli, temiz in AYAR_SETLERI + (AYRILMIS_SETLER if arg.ayrilmis else []):
        sonuc[olayli] = olc(olayli, temiz, setler, bitis, ayrilmis=olayli != "tohum42")
        s = sonuc[olayli]
        print(f"{olayli}: {s['olayli']['puan']['yakalanan']}/{s['olayli']['puan']['olay']} olay, "
              f"olaysız ikizde ayda {s['temiz']['yanlis_alarm']['aylik']} yanlış alarm "
              f"({s['temiz']['yanlis_alarm']['kurala_gore']}), süre {s['olayli']['sure_s']} s")
    if not arg.ayrilmis:
        for olayli, _ in AYRILMIS_SETLER:
            if olayli in onceki.get("setler", {}):
                sonuc[olayli] = onceki["setler"][olayli]

    # Aynı veri ve aynı gün iki kez: aynı uyarılar.
    t = csv_oku(KOK / "data" / "uretilen")
    tekrar = _ozet(izle(t, bitis)) == sonuc["tohum42"]["olayli"]["uyari_ozeti"]

    rapor = {
        "aciklama": "Faz 4 izleme ölçümü. Olaylar ve kurallar aynı kişi tarafından yazıldı: yüksek yakalama oranı "
                    "kuralların olay türlerini bildiğini gösterir, gerçek dünyadaki başarısını göstermez. Bunu "
                    "biraz dengelemek için kurallar tohum 42'de ayarlandı, tohum 43 ve 44'te bir kez ölçüldü.",
        "veri_bitis": bitis.isoformat(),
        "ayarlar": Ayarlar().sozluk(),
        "kabul": {"geri_cagirma_en_az": 0.90, "aylik_yanlis_alarm_en_cok": 2.0},
        "ayni_gun_iki_calisma_ayni": tekrar,
        "setler": dict(sorted(sonuc.items())),
    }
    RAPOR.write_text(json.dumps(rapor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Aynı gün iki çalışma aynı: {tekrar}. Yazıldı: {RAPOR.relative_to(KOK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
