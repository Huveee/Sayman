---
name: demo-rehearsal
description: Starts the Sayman demo with the fine-tuned model and rehearses the presentation questions end to end. Use when the user wants to check that the live demo works.
disable-model-invocation: true
---

Rehearse the live demo. Run no other Ollama job while it runs.

1. **Check Ollama.** Run `ollama list` and confirm that `sayman-ayarli` is listed. If it is missing, stop and tell
   the user. Do not pull or create models without asking.
2. **Start the demo** in the background:
   `.venv/Scripts/python -I scripts/phase10_demo.py baslat`
   Wait until `GET http://127.0.0.1:8010/api/durum` returns `"model_hazir": true`. The first load takes about a
   minute.
3. **Ask the questions.** Log in with `POST /api/giris` (`{"kullanici": ...}`), then ask with `POST /api/sor`
   (`{"soru": ...}`). Do this for one CFO user and one sales-manager user, taking the questions from
   `GET /api/ornek_sorular`. For each answer, record `durum`, the tools called, the seconds taken and the first 200
   characters. Do not fast-forward the clock: the demo state is shared with the presenter.
4. **Check the answers.**
   - CFO questions end with `durum: tamam`.
   - A sales manager asking a cash question gets a refusal (`yetki_yok`), not a number.
   - No answer shows a number the verifier rejected.
5. **Stop the server.** Report the table, and say which questions were slow (over 90 s) or failed.
