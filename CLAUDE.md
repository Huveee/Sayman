# Sayman

AI finance assistant for a Turkish company, built as a capstone. A local Ollama model (`sayman-ayarli`, a QLoRA
fine-tune of qwen3.5:9b) answers questions only through read-only MCP tools over a DuckDB warehouse. A verifier
checks that every number and quote in an answer comes from this turn's tool results. The FastAPI demo embeds the
chat in a fake ERP page. The README (Turkish) is the project report, phase by phase.

# Commands

Windows. Use the Python 3.11 venv with `-I` every time: `.venv/Scripts/python -I ...`. PowerShell takes
`.venv\Scripts\python -I ...`.

- Install: `.venv/Scripts/python -I -m pip install -r requirements.txt`
- All tests: `.venv/Scripts/python -I -m pytest -q -p no:cacheprovider` (about 660 tests, about 5 min). Tests that
  need generated data under `data/` skip with a message naming the script to run.
- One file: `.venv/Scripts/python -I -m pytest -q -p no:cacheprovider tests/test_phase06_verifier.py`
- One test: append `::test_name`.
- Model contract only (3 s): `.venv/Scripts/python -I -m pytest -q -p no:cacheprovider tests/test_model_contract.py`
- Demo: `.venv/Scripts/python -I scripts/phase10_demo.py baslat`, then open http://127.0.0.1:8010. Needs Ollama
  running with `sayman-ayarli`; the fallback is `--model qwen3.5:9b`. Reset the demo clock and state with
  `.venv/Scripts/python -I scripts/phase10_demo.py sifirla`.
- Full report: `.venv/Scripts/python -I scripts/phase11_report.py`. It writes `reports/faz11_rapor_guncel.md`.

# Layout

- `src/sayman/` packages:
  - `data`: synthetic company and simulation clock.
  - `warehouse`: DuckDB and metrics.
  - `tools`: the 14 MCP tools, identity, permissions, scope and audit log.
  - `assistant`: orchestrator, verifier and the Ollama model.
  - `documents`: RAG search and citation check.
  - `monitoring`: rules and alerts.
  - `evaluation`: scoring, oracle and fingerprint.
  - `training`: fine-tune data.
  - `experiment`: Phase 9 generated tools.
  - `model_selection`: Phase 0.
  - `demo`: the web app.
- Scripts are `scripts/phaseNN_<action>.py`, one per README phase. Tests are `tests/test_phaseNN_<topic>.py`.
  Shared question sets, drafts and scoring helpers live in `tests/support/`. Measurement scripts import them too.
- `reports/` holds recorded measurement outputs and is tracked. `data/` holds generated data and is gitignored,
  except `data/kaynak/`.

# Conventions

- Code is English: identifiers, comments, docstrings, file and folder names.
- The product and its model contract stay Turkish: the system prompt, fixed answer texts, tool names, descriptions
  and argument names, JSON output keys, SQL table and column names, UI text, CLI subcommands and flags, and printed
  or logged messages. Never translate a string literal.
- Dataclass fields serialised with `asdict()` keep their Turkish names: orchestrator settings, generator and
  monitoring settings, document records. They are keys in recorded reports and data files.
- Money is `Decimal` in code and integer kuruş in storage (`*_kurus` columns). Never use float for amounts.
- Names in `reports/` and `data/` are historical records. Do not rename them, and never overwrite an earlier
  phase's report: write a new file (example: `faz11_rapor_guncel.md` next to `faz11_rapor.md`).
- New dependencies get an exact pin in `requirements.txt`, at a version released at least two weeks ago.
- In Turkish prose, write "metrik" or "hesaplama" for a calculation. "hesap" means a bank account.
- Commit messages are in Turkish and match the existing history.

# Gotchas

- `tests/test_model_contract.py` freezes everything the model and the browser see. It covers the system prompt,
  the tool definitions, each tool's results for each role, the `/api/*` JSON and the Phase 9 fake-API OpenAPI
  document. If it fails, a refactor leaked into the contract. Fix the code, not the golden. Regenerate the golden
  (`SAYMAN_UPDATE_GOLDEN=1`) only for an intended contract change, and say so.
- Phase 9 tool names come from the route handler names in `src/sayman/experiment/fake_api.py`, through operationId.
  Renaming a handler renames a tool.
- `scripts/verifier_blind_spots.py` runs the old verifier from git commit `6f1a970`. It shims that commit's old
  package paths (`sayman.belge.alinti`).
- `scripts/phase08_kaggle.py` inlines `src/sayman/training/gguf_summary.py` into the Kaggle scripts at the
  `# <<gguf_ozet>>` marker. Keep the marker lines byte-identical.
- Sayman is read-only and never moves money. `tests/test_phase03_tools.py` fails on any writing SQL in the tools
  or metrics.
- The secrets in `.env` are `EVDS_API_KEY`, `SAYMAN_KIMLIK_ANAHTARI`, `KAGGLE_USERNAME` and `KAGGLE_API_TOKEN`.
  Never print them, and never put them in URLs, files or commits. Only open or synthetic data goes to Kaggle.
- Run a held-out question set only once. Before a measurement, unload Ollama models and run no other Ollama jobs.
- README sections written before a measurement ("Ölçümden önce yazılanlar", "Ön kayıt") are pre-registrations. Do
  not edit them, even to update a path. Old-to-new file names are listed in the README rename table.
- `FAZLAR.md` and `docs/ozet/` are personal planning notes. They are gitignored and never referenced from the
  README.
