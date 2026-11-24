---
paths:
  - "scripts/**"
  - "reports/**"
  - "tests/support/**"
  - "kaggle/**"
---

# Measurements and provenance

`RAPOR.md` reports each phase's numbers from files in `reports/`. Keep the trail reproducible.

- Never overwrite or rename a tracked file in `reports/`. A re-run writes a new file. See
  `scripts/phase11_report.py`, whose default output is `reports/faz11_rapor_guncel.md`.
- Question sets in `tests/support/` were frozen before their measurement. Do not edit their questions, expected
  answers or keys. Refactoring identifiers is fine.
- Held-out sets (`saklanan`, `yeni`) are run once. Do not re-run a held-out live measurement.
- Before any live measurement, unload all Ollama models and run no other Ollama job until it ends.
- Replays that need no model are cheap regression checks, provided their outputs go somewhere other than
  `reports/`. Examples: `scripts/verifier_blind_spots.py`, `scripts/phase08b_rescore.py`. They must reproduce
  `reports/kor_nokta_olcum.json` and `reports/faz8b_yeniden_puanlama.json` byte for byte.
- Argparse flags and subcommands are documented in the README, `RAPOR.md` and the demo guide. Keep them.
- Only open or synthetic data may be uploaded to Kaggle.
