"""Orchestrator: question → loop between the model and the tools → verified answer or a fixed text.

Invariant: the text shown to the user is either a model answer that passed the verifier or one of the fixed texts
below (FIXED_TEXTS). A rejected draft is never shown.

Phase 8b, scope: if a verified answer gives a total within a sales role's restricted scope without stating the
scope, the model is asked once for a correction. If the corrected answer states the scope and is verified, that one
is shown. If it does not, or the correction cannot be verified, or the turn hits a limit, the last verified draft is
shown with the fixed scope note appended (SCOPE_NOTES). So the scope rule never turns a verified answer into
"uretemedim".

A turn ends in one of these states:
- `tamam`: the answer was verified.
- `ret`: the identity card is invalid, or all the tool calls in this turn were refused with "yetki_yok". The fixed
  text is returned and the model is not called again: the model cannot try to convey the refused information by
  guessing or in other words. Only "yetki_yok" counts; a schema error, an unknown tool or a tool error is given
  back to the model, which can correct it. If one call is refused and another succeeds in the same step, the loop
  continues.
- `uretemedim`: the answer could not be verified, the model was asked for a correction once, and it still could not
  be verified. An empty or truncated (hit the generation limit) answer also counts as unverified: a cut-off number
  could have passed the rounding rule.
- `adim_siniri`: the limit on model calls (including the correction) or on tool calls was reached. If a correction
  is needed but no step is left, the result is `adim_siniri`: the correction needs one more model call.
- `sure_siniri`: time ran out. It is checked before and after every model call; the remaining time is given to the
  model as the timeout.
- `baglam_siniri`: the messages grew too large to fit in the model's context window. Ollama silently cuts the text
  at the start that does not fit; then the verifier would accept numbers from results the model no longer sees. It
  is estimated before the model call: to the prompt token count the model reported in the previous call (Ollama also
  counts the part read from the cache) only the character-based estimate of the new messages is added (Phase 3
  measurement: ~1.8 characters/token in tool results; a conservative 1.7 here; for document search results the
  measured Turkish article text is 3.1-3.2, 2.8 here). The count the model reports is also checked after the call.
- `model_hatasi`: the model call failed (e.g. Ollama is down).

The identity card does not enter the messages, it goes only to the gate. The card is verified at the start of the
turn for the moment the time limit would expire: the card cannot expire while the turn is running.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

from sayman.tools.catalog import TOOLS
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import IdentityError, verify_card
from sayman.assistant.verifier import SCOPE_NOTES, Verification, verify

FIXED_TEXTS = {
    "kimlik": "Kimlik doğrulanamadı.",
    "ret": "Bu bilgiyi görme yetkiniz yok.",
    "uretemedim": "Bu soruya doğrulanabilir bir cevap üretemedim: cevaptaki bazı sayılar ya da alıntılar araç "
                  "sonuçlarıyla eşleşmedi. Soruyu daha dar sorarak yeniden deneyebilirsiniz.",
    "adim_siniri": "Bu soru izin verilen adım sayısı içinde cevaplanamadı. Soruyu daha küçük parçalara bölerek "
                   "sorabilirsiniz.",
    "sure_siniri": "Bu soru süre sınırı içinde cevaplanamadı. Biraz sonra yeniden deneyebilir ya da soruyu "
                   "daraltabilirsiniz.",
    "baglam_siniri": "Bu soru tek seferde işlenemeyecek kadar çok veri gerektirdi. Soruyu daha küçük parçalara "
                     "bölerek sorabilirsiniz.",
    "model_hatasi": "Dil modeline şu an ulaşılamıyor. Biraz sonra yeniden deneyin.",
}

SYSTEM_PROMPT = """Sen Sayman'sın: bir şirketin yapay zekâ destekli finans asistanısın. Bugünün tarihi {bugun}.
Kurallar:
1. Sorulara yalnızca araçların bu konuşmada döndürdüğü verilerle cevap ver. Gereken veriyi her zaman araçlardan al; rakam uydurma.
2. Sayıları araç sonucundan aynen al. Türkçe biçimde yazabilirsin (75826754.65 → 75.826.754,65 TL) ya da yuvarlayabilirsin (75,8 milyon TL).
3. Kendin toplama, çıkarma, yüzde ya da oran hesaplaması yapma; araç sonucunda olmayan bir sayı yazma.
4. Sözleşme, şirket politikası ya da kanun sorularında belge_ara aracını kullan. belge_ara kullandığında cevabında en az bir alıntı olmalı: maddeden en az üç kelimelik bir bölümü çift tırnak içinde aynen yaz ve hemen ardından parça kimliğini köşeli parantezle ver. Örnek: "fatura tarihinden itibaren 30 gün içinde" [SOZ-S001#m3]
5. Çift tırnağı yalnızca belge alıntısı için kullan; müşteri ya da şirket adlarını tırnak içine alma.
6. Bir araç "yetki_yok" derse bu bilgiyi veremeyeceğini söyle; başka araçla bulmaya çalışma.
7. Sayman salt okunurdur: ödeme, transfer ya da kayıt değişikliği yapamaz.
Kısa ve net Türkçe cevap ver: en çok 120 kelime; listelerde en çok 5 kalem; tablo kullanma."""

CITATION_REASONS = {
    "bilinmeyen_parca": "bu parça bu konuşmada belge_ara ile getirilmedi",
    "cok_kisa": "alıntı en az üç kelime olmalı",
    "metinde_yok": "parçanın metninde aynen geçmiyor",
    "baska_parcada": "bu parçada değil, başka bir parçada geçiyor",
    "alintisiz_atif": "parça kimliği tırnaklı bir alıntı olmadan verilmiş",
    "kimliksiz_alinti": "tırnaklı metnin ardından parça kimliği yok (çift tırnak yalnızca alıntı içindir)",
}


@dataclass(frozen=True)
class Settings:
    en_cok_adim: int = 6             # model calls, including the correction
    en_cok_cagri: int = 8            # tool calls
    duzeltme: int = 1                # how many corrections are requested for an unverifiable answer
    # Phase 0 measurement (this computer, no graphics card): qwen3.5:9b reads the prompt at ~27 tokens/s and writes
    # ~5 tokens/s. A turn with three model calls: ~3,000 new prompt tokens (~110 s) + a 512-token answer (~100 s)
    # + tool calls (~25 s) ≈ 235 s. The limit is 300 s; it can be lowered on a faster computer.
    sure_s: float = 300.0
    baglam_token: int = 8192         # the model's num_ctx value
    cevap_payi: int = 512            # the model's num_predict value
    arac_listesi_token: int = 2400   # Phase 3: 13 tools 2,187 tokens; re-measured with belge_ara (scripts/phase06_smoke.py)
    karakter_token: float = 1.7      # characters/token for estimating tokens in messages (JSON tool results: measured ~1.8)
    belge_karakter_token: float = 2.8  # belge_ara results (Turkish article text: measured 3.1-3.2)


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: list[tuple[str, object]] = field(default_factory=list)
    truncated: bool = False            # hit the generation limit (Ollama done_reason == "length")
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    sure_s: float | None = None


class ModelTimeout(Exception):
    """The model call did not finish in the time it was given."""


@dataclass
class Turn:
    turn_id: str
    status: str
    answer: str                                    # the text shown to the user
    user: str | None = None
    calls: list[dict] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)          # the model's last answer drafts (not shown)
    verifications: list[Verification] = field(default_factory=list)  # the verification of each draft
    model_calls: int = 0
    tokens: list[dict] = field(default_factory=list)
    sure_s: float = 0.0
    reason: str | None = None
    scope_note: bool = False                     # Phase 8b: the fixed scope note was appended to the shown answer
    # Phase 8b measurement: which model call each draft came from, how many tool calls were made before it, and whether
    # it was cut at the length limit (to derive from the record at which draft the old design would stop, and its duration)
    draft_steps: list[dict] = field(default_factory=list)


def tool_definitions() -> list[dict]:
    return [{"type": "function", "function": {"name": a.name, "description": a.description, "parameters": a.schema}}
            for a in TOOLS]


# An unambiguous English-format number: comma thousands groups followed by a dot decimal ("12,396.40"), or at least two
# comma groups ("12,973,882"). A single group without a decimal ("3,294") is a valid Turkish decimal and is not matched.
_ENGLISH_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+)(\.\d+)?(?!\d|[.,]\d)")


def to_turkish_number_format(text: str) -> str:
    """Rewrites unambiguous English-format numbers in a draft into Turkish format: "8,851,941.82" -> "8.851.941,82".

    The verifier reads numbers the Turkish way, so "8,851,941.82" reads as 8,851 and fails although the value came from
    a tool. Only the separators change, never the digits. The draft is verified and shown in Turkish format; the
    model's own message history keeps the raw text.
    """
    def convert(m: re.Match) -> str:
        integer, fraction = m.group(1), m.group(2)
        if fraction is None and integer.count(",") < 2:
            return m.group(0)
        return integer.replace(",", ".") + ("," + fraction[1:] if fraction else "")
    return _ENGLISH_NUMBER.sub(convert, text)


def correction_message(draft: ModelResponse, d: Verification) -> str | None:
    """The message to send to the model if there is something to correct in the draft; otherwise None."""
    lines = []
    if not draft.content.strip():
        lines.append("- Cevap boş geldi.")
    if draft.truncated:
        lines.append("- Cevap uzunluk sınırına takılıp yarıda kesildi; daha kısa yaz.")
    if d.unsourced:
        lines.append("- Şu sayılar bu konuşmadaki araç sonuçlarında yok: "
                        + "; ".join(s.metin for s in d.unsourced) + ".")
    for a in d.invalid_citations:
        subject = f'"{a.quote}"' if a.quote else "alıntı"
        lines.append(f"- {subject} [{a.parca_id}]: {CITATION_REASONS.get(a.reason, a.reason)}.")
    if d.citation_missing:
        lines.append('- belge_ara kullandın ama cevapta geçerli bir alıntı yok: getirilen bir maddeden en az üç '
                        'kelimeyi çift tırnak içinde aynen yaz ve hemen ardından parça kimliğini köşeli parantezle ver '
                        '(ör. "..." [SOZ-S001#m3]).')
    if d.unknown_identifiers:
        lines.append("- Şu kimlikler bu konuşmadaki araç sonuçlarında yok: " + "; ".join(d.unknown_identifiers)
                        + ". Fatura ve çek numaralarını araç sonucundan aynen yaz.")
    if d.ownerless:
        lines.append("- Soru belirli bir müşteriyi soruyor, ama cevaptaki bilgiler o müşteriye ait bir araç "
                        "sonucundan gelmiyor (şirketin ya da portföyün toplamı olabilir). Müşterinin bilgisini "
                        "musteri_adi ile çağırdığın bir araçtan al (ör. musteri_ozeti); müşteri bulunamazsa bunu söyle.")
    if d.missing_scope:
        region = "yurtiçi" if "yurtiçi" in d.missing_scope else "yurt dışı"
        lines.append(f"- Araç sonuçları {d.missing_scope} kapsıyor; cevabında bunu açıkça söyle "
                        f'(ör. "{region} müşterilerinizin ...").')
    if not lines:
        return None
    return ("Cevabın doğrulanamadı:\n" + "\n".join(lines) + "\nCevabı yeniden yaz: yalnızca araç sonuçlarındaki "
            "sayıları kullan, kendin hesaplama yapma. Gerekirse aracı yeniden çağır.")


class Assistant:
    def __init__(self, server, model: Callable[[list[dict], list[dict], float], ModelResponse], settings: Settings = Settings(),
                 clock: Callable[[], float] = time.monotonic, audit_log: AuditLog | None = None):
        """server: SaymanServer (tools are called only through its gate). model: (messages, tool definitions,
        timeout in seconds) -> ModelResponse. audit_log: a separate audit log to which turn summaries are written
        (optional; tool calls are already written to the server's log)."""
        self.server, self.model, self.settings, self.clock, self.audit_log = server, model, settings, clock, audit_log
        self.tools = tool_definitions()

    def _estimate_tokens(self, messages: list[dict], known: tuple[int, int] | None = None) -> int:
        """Token estimate of the next prompt. If the model reported the prompt token count in the previous call, that
        count (plus the tokens it generated) is the basis and only the messages added since are estimated from
        characters; otherwise the tool list measurement plus the character-based estimate of all messages is used."""
        def estimate(items):
            return int(sum(len(json.dumps(m, ensure_ascii=False))
                           / (self.settings.belge_karakter_token if m.get("tool_name") == "belge_ara"
                              else self.settings.karakter_token) for m in items))

        if known is not None:
            known_tokens, seen_messages = known
            return known_tokens + estimate(messages[seen_messages:])
        return self.settings.arac_listesi_token + estimate(messages)

    def ask(self, question: str, card) -> Turn:
        a, s = self.settings, self.server
        started = self.clock()
        deadline = started + a.sure_s
        turn = Turn(turn_id="T" + secrets.token_hex(6), status="", answer="")

        scope_noted: tuple[str, str] | None = None  # (the last verified draft whose only flaw is a missing scope, the scope)

        def finish(status: str, answer: str | None = None, reason: str | None = None) -> Turn:
            if status != "tamam" and scope_noted is not None:  # there is a verified draft: show it with the scope note
                status, answer, reason = "tamam", scope_noted[0] + "\n\n" + SCOPE_NOTES[scope_noted[1]], f"kapsam_notu:{status}"
                turn.scope_note = True
            turn.status, turn.reason = status, reason
            turn.answer = answer if answer is not None else FIXED_TEXTS[status]
            turn.sure_s = round(self.clock() - started, 3)
            if self.audit_log is not None:
                last_verification = turn.verifications[-1] if turn.verifications else None
                self.audit_log.write(tur_id=turn.turn_id, kullanici=turn.user, soru=question[:500], durum=status,
                               neden=reason, model_cagrisi=turn.model_calls,
                               cagri_idleri=[c["yanit"].get("cagri_id") for c in turn.calls],
                               taslak_sayisi=len(turn.drafts),
                               kaynaksiz=[x.metin for x in last_verification.unsourced] if last_verification else [],
                               gecersiz_alinti=[x.reason for x in last_verification.invalid_citations] if last_verification else [],
                               istem_token=[t["istem"] for t in turn.tokens], sure_s=turn.sure_s)
            return turn

        try:
            identity = verify_card(card, s.key, s.permissions.users, s.time_fn() + a.sure_s)
        except IdentityError as e:
            return finish("ret", FIXED_TEXTS["kimlik"], reason=e.reason)
        turn.user = identity.user
        today: dt.date = s.clock().simdi
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(bugun=today.isoformat())},
                    {"role": "user", "content": question}]
        corrections = 0
        known = None  # (the prompt + generated tokens the model last reported, how many messages there were in that call)
        while True:
            if turn.model_calls >= a.en_cok_adim:
                return finish("adim_siniri", reason="model_cagrisi")
            remaining = deadline - self.clock()
            if remaining <= 0:
                return finish("sure_siniri")
            if self._estimate_tokens(messages, known) > a.baglam_token - a.cevap_payi:
                return finish("baglam_siniri")
            message_count = len(messages)
            try:
                c = self.model(messages, self.tools, remaining)
            except ModelTimeout:
                return finish("sure_siniri")
            except Exception as e:  # the details go only to the log
                return finish("model_hatasi", reason=f"{type(e).__name__}: {str(e)[:200]}")
            turn.model_calls += 1
            turn.tokens.append({"istem": c.prompt_tokens, "uretim": c.generated_tokens, "sure_s": c.sure_s})
            if self.clock() > deadline:
                return finish("sure_siniri")
            if c.prompt_tokens is not None and c.prompt_tokens > a.baglam_token - a.cevap_payi:
                return finish("baglam_siniri", reason="istem_token")  # the estimate was wrong; the model may have seen a truncated context
            if c.prompt_tokens is not None:
                known = (c.prompt_tokens + (c.generated_tokens or 0), message_count)

            if c.tool_calls:
                if len(turn.calls) + len(c.tool_calls) > a.en_cok_cagri:
                    return finish("adim_siniri", reason="arac_cagrisi")
                messages.append({"role": "assistant", "content": c.content,
                                 "tool_calls": [{"function": {"name": name, "arguments": arg}}
                                                for name, arg in c.tool_calls]})
                for name, arg in c.tool_calls:
                    response, is_error = s.gate(name, arg, card)
                    turn.calls.append({"arac": name, "argumanlar": arg if isinstance(arg, dict) else {},
                                         "hata_mi": is_error, "yanit": response})
                    messages.append({"role": "tool", "tool_name": name if isinstance(name, str) else repr(name),
                                     "content": json.dumps(response, ensure_ascii=False)})
                if all(x["hata_mi"] and x["yanit"].get("hata") == "yetki_yok" for x in turn.calls):
                    return finish("ret", reason="yetki_yok")
                continue

            content = to_turkish_number_format(c.content)
            d = verify(content, question, turn.calls, today)
            turn.drafts.append(content)
            turn.verifications.append(d)
            turn.draft_steps.append({"cagri_no": turn.model_calls, "arac_cagrisi": len(turn.calls),
                                        "kesildi": bool(c.truncated)})
            message = correction_message(c, d)
            if message is None:
                scope_noted = None
                return finish("tamam", content.strip())
            if d.gecerli and content.strip() and not c.truncated:  # only the scope is missing
                scope_noted = (content.strip(), d.missing_scope)
            if corrections >= a.duzeltme:
                return finish("uretemedim")
            corrections += 1
            messages += [{"role": "assistant", "content": c.content}, {"role": "user", "content": message}]
