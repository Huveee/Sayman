"""Tests of the Phase 11 report script (scripts/phase11_report.py).

It does not go to Ollama or the internet; it reads the measurement files under reports/. It never overwrites the real
output files: every run writes to a temporary folder. The tests for source changes and missing files work on a
temporary copy of the manifest files.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase11_report.py"
REPORTS = ROOT / "reports"

_spec = importlib.util.spec_from_file_location("phase11_report", SCRIPT)
report_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report_script)


def run_script(output: Path, folder: Path = REPORTS):
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--rapor-klasoru", str(folder), "--cikti", str(output)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )


def find_row(data: dict, table_id: str, name: str) -> dict:
    for section in data["bolumler"]:
        for t in section["tablolar"]:
            if t["id"] == table_id:
                return next(r for r in t["satirlar"] if r["ad"] == name)
    raise AssertionError(f"tablo yok: {table_id}")


@pytest.fixture()
def report_copy(tmp_path):
    """A temporary copy of only the files in the manifest."""
    folder = tmp_path / "reports"
    folder.mkdir()
    for name in report_script.manifest_files():
        shutil.copy(REPORTS / name, folder / name)
    return folder


def test_real_command_runs_and_known_values_are_present(tmp_path):
    output = tmp_path / "rapor.md"
    s = run_script(output)
    assert s.returncode == 0, s.stderr
    data = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    md = output.read_text(encoding="utf-8")
    # Values verified from the files
    assert find_row(data, "faz9_kollar", "Tüm set, gösterilen doğru")["degerler"] == [[65, 86], [18, 86], [68, 86]]
    assert find_row(data, "faz9_birincil", "Kesin McNemar p (iki yönlü)")["degerler"] == [0.049]
    assert find_row(data, "faz5_arama", "Anlam araması (bge-m3)")["degerler"][1] == 1.0
    assert find_row(data, "faz5_arama", "Kelime + anlam + sıralayıcı (kabul yöntemi)")["gosterim"][1] == "0,981"
    assert find_row(data, "faz7_hedefler", "Araç seçimi")["degerler"] == [0.85, 0.9167, 0.8733, True]
    assert find_row(data, "faz8_saklanan", "Belge cevabı")["degerler"][1:] == [0.325, 0.3, 0.875]
    assert find_row(data, "faz4_izleme", "Tohum 43")["degerler"][-1] == 1.843
    assert find_row(data, "faz2_metrikler", "Nakit pozisyonu (TL)")["gosterim"] == ["75.826.754,65"]
    assert find_row(data, "faz0_hiz", "Üretim (token/sn)")["degerler"] == [8.48, 5.11]
    assert find_row(data, "faz6_durumlar", "İlk çalıştırma")["gosterim"] == ["6", "1", "1", "5", "0"]  # missing counter = 0
    # The source file name is written under every table
    for section in data["bolumler"]:
        for t in section["tablolar"]:
            assert t["kaynaklar"] and all(f"`{k}`" in md for k in t["kaynaklar"])
    assert 5 <= len(data["sonuclar"]) <= 10
    assert "## Sonuçlar ve sınırlamalar" in md


def test_table_and_conclusion_change_when_source_value_changes(report_copy, tmp_path):
    path = report_copy / "faz9_karsilastirma.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["kollar"]["el"]["tum_set"]["dogru"] = 66
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "cikti" / "rapor.md"
    s = run_script(output, report_copy)
    assert s.returncode == 0, s.stderr
    result = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert find_row(result, "faz9_kollar", "Tüm set, gösterilen doğru")["degerler"][0] == [66, 86]
    assert "66/86" in output.read_text(encoding="utf-8")
    assert any("66/86" in x["metin"] for x in result["sonuclar"])
    # The table read from another file was not affected
    assert find_row(result, "faz5_arama", "Anlam araması (bge-m3)")["degerler"][1] == 1.0


@pytest.mark.parametrize("filename", ["faz5_arama.json", "faz9_karsilastirma.json", "faz6_duman_secili.json"])
def test_missing_file_gives_nonzero_exit_and_name(report_copy, tmp_path, filename):
    (report_copy / filename).unlink()
    output = tmp_path / "rapor.md"
    s = run_script(output, report_copy)
    assert s.returncode != 0
    assert filename in s.stderr
    assert not output.exists() and not output.with_suffix(".json").exists()  # no half-written output


def test_missing_key_gives_nonzero_exit_and_path(report_copy, tmp_path):
    path = report_copy / "faz9_karsilastirma.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["birincil"]["mcnemar_p"]
    path.write_text(json.dumps(data), encoding="utf-8")
    s = run_script(tmp_path / "rapor.md", report_copy)
    assert s.returncode != 0
    assert "faz9_karsilastirma.json" in s.stderr and "birincil.mcnemar_p" in s.stderr


def test_parent_dict_is_required_for_counter(report_copy, tmp_path):
    """A missing status counter counts as 0, but an error is raised if the statuses dictionary itself is missing."""
    path = report_copy / "faz6_duman_ilk.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["ozet"]["durumlar"]
    path.write_text(json.dumps(data), encoding="utf-8")
    s = run_script(tmp_path / "rapor.md", report_copy)
    assert s.returncode != 0 and "faz6_duman_ilk.json" in s.stderr


def test_two_runs_are_identical(tmp_path):
    a, b = tmp_path / "a" / "rapor.md", tmp_path / "b" / "rapor.md"
    assert run_script(a).returncode == 0 and run_script(b).returncode == 0
    assert a.read_bytes() == b.read_bytes()
    assert a.with_suffix(".json").read_bytes() == b.with_suffix(".json").read_bytes()
    assert not re.search(r"\d{2}:\d{2}:\d{2}", a.read_text(encoding="utf-8"))  # no timestamp
