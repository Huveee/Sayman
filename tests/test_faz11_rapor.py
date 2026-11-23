"""Faz 11 rapor betiğinin testleri (scripts/faz11_rapor.py).

Ollama'ya ve internete gitmez; reports/ altındaki ölçüm dosyalarını okur. Gerçek çıktı dosyalarının üzerine yazılmaz:
bütün çalıştırmalar geçici klasöre yazar. Kaynak değişikliği ve eksik dosya testleri, manifestteki dosyaların geçici
kopyası üzerinde yapılır.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

KOK = Path(__file__).resolve().parents[1]
BETIK = KOK / "scripts" / "faz11_rapor.py"
RAPORLAR = KOK / "reports"

_spec = importlib.util.spec_from_file_location("faz11_rapor", BETIK)
rapor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rapor)


def calistir(cikti: Path, klasor: Path = RAPORLAR):
    return subprocess.run(
        [sys.executable, "-I", str(BETIK), "--rapor-klasoru", str(klasor), "--cikti", str(cikti)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(KOK),
    )


def satir(veri: dict, tablo_id: str, ad: str) -> dict:
    for bolum in veri["bolumler"]:
        for t in bolum["tablolar"]:
            if t["id"] == tablo_id:
                return next(r for r in t["satirlar"] if r["ad"] == ad)
    raise AssertionError(f"tablo yok: {tablo_id}")


@pytest.fixture()
def kopya(tmp_path):
    """Yalnızca manifestteki dosyaların geçici kopyası."""
    klasor = tmp_path / "reports"
    klasor.mkdir()
    for ad in rapor.manifest_dosyalari():
        shutil.copy(RAPORLAR / ad, klasor / ad)
    return klasor


def test_gercek_komut_calisir_ve_bilinen_degerler_var(tmp_path):
    cikti = tmp_path / "rapor.md"
    s = calistir(cikti)
    assert s.returncode == 0, s.stderr
    veri = json.loads(cikti.with_suffix(".json").read_text(encoding="utf-8"))
    md = cikti.read_text(encoding="utf-8")
    # Dosyalardan doğrulanan değerler
    assert satir(veri, "faz9_kollar", "Tüm set, gösterilen doğru")["degerler"] == [[65, 86], [18, 86], [68, 86]]
    assert satir(veri, "faz9_birincil", "Kesin McNemar p (iki yönlü)")["degerler"] == [0.049]
    assert satir(veri, "faz5_arama", "Anlam araması (bge-m3)")["degerler"][1] == 1.0
    assert satir(veri, "faz5_arama", "Kelime + anlam + sıralayıcı (kabul yöntemi)")["gosterim"][1] == "0,981"
    assert satir(veri, "faz7_hedefler", "Araç seçimi")["degerler"] == [0.85, 0.9167, 0.8733, True]
    assert satir(veri, "faz8_saklanan", "Belge cevabı")["degerler"][1:] == [0.325, 0.3, 0.875]
    assert satir(veri, "faz4_izleme", "Tohum 43")["degerler"][-1] == 1.843
    assert satir(veri, "faz2_metrikler", "Nakit pozisyonu (TL)")["gosterim"] == ["75.826.754,65"]
    assert satir(veri, "faz0_hiz", "Üretim (token/sn)")["degerler"] == [8.48, 5.11]
    assert satir(veri, "faz6_durumlar", "İlk çalıştırma")["gosterim"] == ["6", "1", "1", "5", "0"]  # eksik sayaç = 0
    # Her tablonun altında kaynak dosya adı yazılı
    for bolum in veri["bolumler"]:
        for t in bolum["tablolar"]:
            assert t["kaynaklar"] and all(f"`{k}`" in md for k in t["kaynaklar"])
    assert 5 <= len(veri["sonuclar"]) <= 10
    assert "## Sonuçlar ve sınırlamalar" in md


def test_kaynak_degeri_degisince_tablo_ve_sonuc_degisir(kopya, tmp_path):
    yol = kopya / "faz9_karsilastirma.json"
    veri = json.loads(yol.read_text(encoding="utf-8"))
    veri["kollar"]["el"]["tum_set"]["dogru"] = 66
    yol.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    cikti = tmp_path / "cikti" / "rapor.md"
    s = calistir(cikti, kopya)
    assert s.returncode == 0, s.stderr
    sonuc = json.loads(cikti.with_suffix(".json").read_text(encoding="utf-8"))
    assert satir(sonuc, "faz9_kollar", "Tüm set, gösterilen doğru")["degerler"][0] == [66, 86]
    assert "66/86" in cikti.read_text(encoding="utf-8")
    assert any("66/86" in x["metin"] for x in sonuc["sonuclar"])
    # Başka bir dosyadan okunan tablo etkilenmedi
    assert satir(sonuc, "faz5_arama", "Anlam araması (bge-m3)")["degerler"][1] == 1.0


@pytest.mark.parametrize("dosya", ["faz5_arama.json", "faz9_karsilastirma.json", "faz6_duman_secili.json"])
def test_eksik_dosya_sifirdan_farkli_cikis_ve_ad(kopya, tmp_path, dosya):
    (kopya / dosya).unlink()
    cikti = tmp_path / "rapor.md"
    s = calistir(cikti, kopya)
    assert s.returncode != 0
    assert dosya in s.stderr
    assert not cikti.exists() and not cikti.with_suffix(".json").exists()  # yarım çıktı yazılmaz


def test_eksik_anahtar_sifirdan_farkli_cikis_ve_yol(kopya, tmp_path):
    yol = kopya / "faz9_karsilastirma.json"
    veri = json.loads(yol.read_text(encoding="utf-8"))
    del veri["birincil"]["mcnemar_p"]
    yol.write_text(json.dumps(veri), encoding="utf-8")
    s = calistir(tmp_path / "rapor.md", kopya)
    assert s.returncode != 0
    assert "faz9_karsilastirma.json" in s.stderr and "birincil.mcnemar_p" in s.stderr


def test_sayac_icin_ust_sozluk_zorunlu(kopya, tmp_path):
    """Eksik durum sayacı 0 sayılır, ama durumlar sözlüğünün kendisi yoksa hata verilir."""
    yol = kopya / "faz6_duman_ilk.json"
    veri = json.loads(yol.read_text(encoding="utf-8"))
    del veri["ozet"]["durumlar"]
    yol.write_text(json.dumps(veri), encoding="utf-8")
    s = calistir(tmp_path / "rapor.md", kopya)
    assert s.returncode != 0 and "faz6_duman_ilk.json" in s.stderr


def test_iki_calisma_birebir_ayni(tmp_path):
    a, b = tmp_path / "a" / "rapor.md", tmp_path / "b" / "rapor.md"
    assert calistir(a).returncode == 0 and calistir(b).returncode == 0
    assert a.read_bytes() == b.read_bytes()
    assert a.with_suffix(".json").read_bytes() == b.with_suffix(".json").read_bytes()
    assert not re.search(r"\d{2}:\d{2}:\d{2}", a.read_text(encoding="utf-8"))  # saat damgası yok
