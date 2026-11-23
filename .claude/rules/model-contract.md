---
paths:
  - "src/sayman/tools/**"
  - "src/sayman/assistant/**"
  - "src/sayman/experiment/**"
  - "src/sayman/training/**"
  - "src/sayman/demo/**"
  - "tests/golden/**"
---

# Model contract

The fine-tuned model `sayman-ayarli` was trained on the exact text it sees. In these packages, a change to any of
the following silently degrades the live demo. No unit test notices, only `tests/test_model_contract.py`:

- The text of `SYSTEM_PROMPT` and `FIXED_TEXTS` in `src/sayman/assistant/orchestrator.py`.
- Tool names, descriptions, JSON-schema argument names, enums and defaults in `src/sayman/tools/catalog.py`.
- Keys and value formats of every tool result, including the gate's envelope (`cagri_id`, `arac`, `tarih`,
  `kapsam`, `sonuc`, `kaynak`, `hata`, `mesaj`). The verifier types output fields by name: fields ending in `_no`
  are record numbers.
- The browser API in `src/sayman/demo/app.py` (`/api/*` paths and JSON keys), used by `static/app.js`.
- The route handlers, pydantic models and summaries in `src/sayman/experiment/fake_api.py`. They generate the
  Phase 9 tool names and descriptions.
- The prompt rendering in `src/sayman/training/prompt.py`. Training examples must match what Ollama renders at
  inference.

Python identifiers around these strings can be renamed freely. After any edit here, run
`.venv/Scripts/python -I -m pytest -p no:cacheprovider tests/test_model_contract.py`.

Changing the contract on purpose means retraining or re-measuring. Ask the user first.
