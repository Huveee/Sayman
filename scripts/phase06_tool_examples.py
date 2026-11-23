"""Faz 6: doğrulayıcının taslak testleri için gerçek araç sonuçlarını kaydeder.

Doğrulayıcı yazılmadan önce, gerçek araçlar gerçek ambar üzerinde bir kez çağrılır ve sonuçları
tests/support/phase06_tool_results.json dosyasına yazılır. Taslak cevaplar (tests/faz6_taslaklar.py) bu sonuçlardaki
sayılara göre elle yazılır; testler ambarı açmadan bu dosyayı okur.

Kimlik kartları bu çalıştırmaya özel rastgele bir anahtarla imzalanır; .env'deki anahtar kullanılmaz.

Girdi:  data/ambar/sayman.duckdb (faz2_ambar_kur.py), data/saat.json
Çıktı:  tests/support/phase06_tool_results.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz6_arac_ornekleri.py
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sys
import tempfile
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.tools.audit_log import DenetimKaydi  # noqa: E402
from sayman.tools.identity import kart_uret  # noqa: E402
from sayman.tools.server import SaymanSunucusu  # noqa: E402
from sayman.tools.permissions import yetki_oku  # noqa: E402
from sayman.data.clock import saat_oku  # noqa: E402

CIKTI = KOK / "tests" / "support/phase06_tool_results.json"
CFO, SATIS = "selin.aydin", "murat.kaya"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    saat = saat_oku(KOK / "data" / "saat.json")
    anahtar = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory() as gecici:
        sunucu = SaymanSunucusu(ambar=KOK / "data" / "ambar" / "sayman.duckdb", saat=lambda: saat, anahtar=anahtar,
                                yetki=yetki_oku(), kayit=DenetimKaydi(Path(gecici) / "kayit.jsonl"),
                                kapsam_klasoru=Path(gecici) / "kapsam")
        try:
            def cagir(kullanici, arac, **argumanlar):
                yanit, hata = sunucu.kapi(arac, argumanlar, kart_uret(kullanici, anahtar))
                return {"kullanici": kullanici, "arac": arac, "argumanlar": argumanlar, "hata_mi": hata,
                        "yanit": yanit}

            yas = cagir(CFO, "alacak_yaslandirma", en_fazla=5)
            a, b = (m["musteri"] for m in yas["yanit"]["sonuc"]["en_buyuk_alacaklar"][:2])
            bugun = saat.simdi
            cagrilar = {
                "yaslandirma": yas,
                "nakit": cagir(CFO, "nakit_pozisyonu"),
                "musteri_a": cagir(CFO, "musteri_ozeti", musteri_adi=a),
                "musteri_b": cagir(CFO, "musteri_ozeti", musteri_adi=b),
                "geciken": cagir(CFO, "geciken_alacaklar", min_gun=30, en_fazla=5),
                "kur_riski": cagir(CFO, "kur_riski"),
                "dso": cagir(CFO, "dso_hesapla", donem="son_90_gun"),
                "yaklasan": cagir(CFO, "yaklasan_odemeler", gun_sayisi=30, en_fazla=5),
                "cikislar": cagir(CFO, "banka_hareketleri", baslangic=(bugun - dt.timedelta(days=7)).isoformat(),
                                  bitis=bugun.isoformat(), yon="cikis", en_fazla=5),
                "usd": cagir(CFO, "doviz_kuru", para_birimi="USD"),
                "satis_nakit_ret": cagir(SATIS, "nakit_pozisyonu"),
            }
        finally:
            sunucu.kapat()
    veri = {"aciklama": "Gerçek araçların gerçek ambardaki sonuçları; doğrulayıcı taslak testleri bunlara dayanır.",
            "bugun": saat.simdi.isoformat(), "cagrilar": cagrilar}
    CIKTI.write_text(json.dumps(veri, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{CIKTI.name}: {len(cagrilar)} çağrı, {CIKTI.stat().st_size:,} bayt")


if __name__ == "__main__":
    main()
