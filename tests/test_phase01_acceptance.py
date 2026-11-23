"""Phase 1 acceptance test: on tables generated from the real UCI data.

scripts/phase01_download_uci.py and scripts/phase01_generate_data.py must have been run first; otherwise the tests are skipped.
Tables are read from the CSVs on disk; the numbers in the report are not trusted, everything is recomputed.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sayman.data import invariants, calibration
from sayman.data.table_io import read_csv, file_digest, data_digest
from sayman.data.clock import read_clock
from sayman.data.generator import GeneratorSettings

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "uretilen"
REPORT = ROOT / "reports" / "faz1_veri.json"
ANSWER_KEY = ROOT / "data" / "anahtar" / "olaylar.json"

pytestmark = pytest.mark.skipif(not (GENERATED.exists() and REPORT.exists() and ANSWER_KEY.exists()),
                                reason="Önce phase01_download_uci.py ve phase01_generate_data.py çalıştırılmalı")


@pytest.fixture(scope="module")
def report():
    return json.loads(REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tables():
    return read_csv(GENERATED)


@pytest.fixture(scope="module")
def answer_key():
    return json.loads(ANSWER_KEY.read_text(encoding="utf-8"))


def test_hash_of_tables_on_disk_matches_the_report(tables, report):
    assert data_digest(tables)[0] == report["veri_hash"]


def test_input_files_are_unchanged(report):
    for name, path in (("uci_ham", "data/ara/uci_ham.csv.gz"), ("tcmb_kurlar", "data/kaynak/tcmb_kurlar.csv"),
                    ("kalibrasyon", "data/kaynak/kalibrasyon.json")):
        assert file_digest(ROOT / path, as_text=not path.endswith(".gz")) == report["girdi_sha256"][name], name


def test_same_seed_in_a_separate_process_gives_same_hash(report):
    output = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "phase01_generate_data.py"), "--yalniz-hash"],
                           capture_output=True, text=True, check=True, timeout=900)
    assert output.stdout.strip() == report["veri_hash"]


def test_cleaning_reconciliation(report):
    t = report["temizleme"]
    assert t["mutabakat"] and t["girdi"] == sum(t["tutulan"].values()) + sum(t["atilan"].values())
    assert t["tutulan"]["iade"] > 0 and t["tutulan"]["perakende"] > 0


def test_all_checks_pass(tables, answer_key):
    settings = GeneratorSettings()
    result = invariants.run_all(tables, answer_key, settings.olay_sayilari, settings.simdi, settings.veri_bitis)
    assert {name: h for name, h in result.items() if h} == {}


def test_within_calibration_bands(tables):
    source = json.loads((ROOT / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))
    rows = calibration.compare_with_bands(calibration.measure(tables, GeneratorSettings().veri_bitis, 2025), source)
    assert [s for s in rows if not s["tamam"]] == []


def test_clock_and_answer_key_are_not_in_the_folder_tools_read():
    assert read_clock(ROOT / "data" / "saat.json").simdi == GeneratorSettings().simdi
    assert not any(p.suffix == ".json" for p in GENERATED.iterdir())
    assert ANSWER_KEY.parent != GENERATED
