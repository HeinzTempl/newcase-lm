"""
Kanzlei Pipeline v2 - LLM Zusammenfassung
==========================================
Klartext-First Ansatz:
  - Stufe 2: Klartext-Zusammenfassungen (keine Anonymisierung)
  - Stufe 3a: Klartext-Gesamtübersicht (inhouse)
  - Stufe 3b: Anonymisierung der Gesamtübersicht (cloud-ready)
"""

import json
import logging
import re
import time
import requests
from pathlib import Path

from config import (
    NEWCASE_BACKEND,
    NEWCASE_API_BASE_URL,
    NEWCASE_API_KEY,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
    MAIL_SYSTEM_PROMPT,
    MAIL_USER_PROMPT_TEMPLATE,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT_TEMPLATE,
    WRITE_SYSTEM_PROMPT,
    WRITE_USER_PROMPT_TEMPLATE,
    ACT_SUMMARY_SYSTEM_PROMPT,
    ANON_SYSTEM_PROMPT,
    ANON_USER_PROMPT_TEMPLATE,
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_USER_PROMPT_TEMPLATE,
    MAX_TEXT_LENGTH,
    MAX_VERIFICATION_RETRIES,
    ENABLE_VERIFICATION,
    ENABLE_TWO_STAGE,
    NUM_CTX,
    ANON_BACKEND,
    ANON_BASE_URL,
    ANON_API_KEY,
    ANON_MODEL,
)
import pseudonymizer


# Dateiendungen, bei denen wir den E-Mail-Spezialprompt verwenden
MAIL_EXTENSIONS = {".msg", ".eml"}

logger = logging.getLogger(__name__)


def check_backend_available() -> bool:
    """Prüft, ob das konfigurierte Backend bereit ist."""
    if NEWCASE_BACKEND == "openai_compat":
        return _check_openai_compat_available()
    return _check_ollama_available()


# Backwards-Compat-Alias (für ältere Aufrufer wie pipeline.py)
def check_ollama_available() -> bool:
    return check_backend_available()


def _check_ollama_available() -> bool:
    """Prüft ob Ollama läuft und das Modell verfügbar ist."""
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            logger.error("Ollama antwortet nicht")
            return False

        models = resp.json().get("models", [])
        model_names = [m["name"] for m in models]

        if not any(OLLAMA_MODEL in name for name in model_names):
            logger.error(
                f"Modell '{OLLAMA_MODEL}' nicht gefunden. "
                f"Verfügbare Modelle: {model_names}"
            )
            logger.info(f"Installiere mit: ollama pull {OLLAMA_MODEL}")
            return False

        logger.info(f"Ollama OK - Modell '{OLLAMA_MODEL}' verfügbar")
        return True

    except requests.ConnectionError:
        logger.error(
            "Ollama nicht erreichbar. Starte Ollama mit: ollama serve"
        )
        return False


def _api_base() -> str:
    """Normalisiert NEWCASE_API_BASE_URL auf die reine Base-URL.

    Toleriert, wenn versehentlich der volle Endpoint gesetzt wurde
    (z.B. https://api.mistral.ai/v1/chat/completions) — sonst würde daraus
    .../chat/completions/chat/completions und die API antwortet mit 400/404.
    """
    base = (NEWCASE_API_BASE_URL or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/chat/completion", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def _check_openai_compat_available() -> bool:
    """Prüft, ob die Cloud-API-Konfiguration nutzbar ist.

    Sanity-Check der Env-Vars plus ein kostenloser GET /models — damit ein
    falscher Modellname sofort auffällt und nicht erst als HTTP 400 mitten im Lauf.
    """
    if not NEWCASE_API_KEY:
        logger.error(
            "NEWCASE_API_KEY ist nicht gesetzt — Cloud-Backend nicht nutzbar."
        )
        return False
    if not NEWCASE_API_BASE_URL:
        logger.error(
            "NEWCASE_API_BASE_URL ist nicht gesetzt — z.B. https://api.mistral.ai/v1"
        )
        return False

    base = _api_base()

    # Modell-Preflight (kostet keine Tokens). Ein fehlschlagender Aufruf ist
    # kein K.O. — nicht jedes Backend bietet /models an.
    try:
        resp = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {NEWCASE_API_KEY}"},
            timeout=15,
        )
        if resp.status_code in (401, 403):
            logger.error(
                f"Cloud-API lehnt den API-Key ab ({resp.status_code}) — "
                f"NEWCASE_API_KEY prüfen ({base})."
            )
            return False
        if resp.ok:
            ids = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
            if ids and OLLAMA_MODEL not in ids:
                stem = OLLAMA_MODEL.split(":")[0][:4].lower()
                hint = [m for m in ids if stem in m.lower()] or sorted(ids)
                logger.error(
                    f"Modell '{OLLAMA_MODEL}' kennt {base} nicht — genau das gibt "
                    f"beim Lauf einen HTTP 400. NEWCASE_MODEL auf einen gültigen "
                    f"Wert setzen, z.B.: {', '.join(hint[:5])}"
                )
                return False
    except requests.RequestException as e:
        logger.warning(
            f"Modell-Preflight übersprungen ({base}/models nicht abrufbar: {e})"
        )

    logger.info(f"Cloud-Backend OK — {base} mit Modell '{OLLAMA_MODEL}'")
    return True


def summarize_document(extracted: dict) -> dict:
    """
    Fasst ein einzelnes Dokument im KLARTEXT zusammen (keine Anonymisierung).

    Bei E-Mail-Dokumenten (.msg/.eml) wird automatisch ein spezialisierter
    Prompt verwendet, der eine Header-Tabelle (Datum/Von/An/Betreff) plus
    narrative Zusammenfassung liefert — damit die Gesamtübersicht (Stage 3a)
    nachher klar weiß, wer wann an wen geschrieben hat.

    Returns:
        dict mit "summary", "verified", "issues"
    """
    text = extracted["extracted_text"]
    file_type = extracted.get("file_type", "").lower()
    is_mail = file_type in MAIL_EXTENSIONS

    # Text kürzen wenn zu lang
    if len(text) > MAX_TEXT_LENGTH:
        logger.warning(
            f"{extracted['source_file']}: Text gekürzt von "
            f"{len(text)} auf {MAX_TEXT_LENGTH} Zeichen"
        )
        text = text[:MAX_TEXT_LENGTH] + "\n\n[... Text gekürzt ...]"

    # === Prompt-Auswahl: E-Mail oder Standard ===
    if is_mail:
        system_prompt = MAIL_SYSTEM_PROMPT
        user_prompt = MAIL_USER_PROMPT_TEMPLATE.format(document_text=text)
        label_prefix = "E-Mail"
    else:
        system_prompt = SUMMARY_SYSTEM_PROMPT
        user_prompt = SUMMARY_USER_PROMPT_TEMPLATE.format(document_text=text)
        label_prefix = "Zusammenfassung"

    # === Zusammenfassung im Klartext ===
    if ENABLE_TWO_STAGE and not is_mail:
        # Two-Stage-Prompting: Pass 1 = Fakten extrahieren, Pass 2 = Sachverhalt formulieren.
        # Bei E-Mails behalten wir den dedizierten Mail-Prompt (Header-Tabelle + Inhalt),
        # weil der bereits eine andere Strukturierung vornimmt.
        logger.info(f"  [Two-Stage] Pass 1/2 — Faktenextraktion ...")
        facts = _call_llm(
            system_prompt=EXTRACT_SYSTEM_PROMPT,
            user_prompt=EXTRACT_USER_PROMPT_TEMPLATE.format(document_text=text),
            label=f"Extract: {extracted['source_file']}",
        )

        if facts.startswith("[FEHLER"):
            return {"summary": facts, "verified": False, "issues": ["LLM-Fehler bei Faktenextraktion"]}

        logger.info(f"  [Two-Stage] Pass 2/2 — Sachverhalt formulieren ...")
        summary = _call_llm(
            system_prompt=WRITE_SYSTEM_PROMPT,
            user_prompt=WRITE_USER_PROMPT_TEMPLATE.format(facts=facts),
            label=f"Write: {extracted['source_file']}",
        )
    else:
        summary = _call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            label=f"{label_prefix}: {extracted['source_file']}",
        )

    if summary.startswith("[FEHLER"):
        return {"summary": summary, "verified": False, "issues": ["LLM-Fehler"]}

    # === Verifikationsschleife (optional) ===
    if not ENABLE_VERIFICATION:
        logger.info(f"  Verifikation deaktiviert – Zusammenfassung direkt übernommen")
        return {"summary": summary, "verified": True, "issues": []}

    verified = False
    issues = []

    for attempt in range(MAX_VERIFICATION_RETRIES):
        logger.info(f"  Verifikation (Versuch {attempt + 1}/{MAX_VERIFICATION_RETRIES})...")

        verification_result = _verify_summary(text, summary)

        if verification_result["ok"]:
            verified = True
            logger.info(f"  ✓ Zusammenfassung verifiziert")
            break

        issues = verification_result["issues"]
        logger.warning(f"  ✗ {len(issues)} Problem(e) gefunden:")
        for issue in issues:
            logger.warning(f"    - {issue}")

        # Zusammenfassung korrigieren lassen
        summary = _fix_summary(text, summary, issues)
        logger.info(f"  → Korrigierte Zusammenfassung erstellt")

    if not verified:
        logger.warning(f"  ⚠ Zusammenfassung konnte nach {MAX_VERIFICATION_RETRIES} Versuchen nicht vollständig verifiziert werden")
        summary = f"[⚠ NICHT VOLLSTÄNDIG VERIFIZIERT - bitte manuell prüfen]\n\n{summary}"

    return {"summary": summary, "verified": verified, "issues": issues}


def _verify_summary(original_text: str, summary: str) -> dict:
    """
    Prüft eine Zusammenfassung gegen den Originaltext.
    Gibt {"ok": bool, "issues": list[str]} zurück.
    """
    prompt = VERIFICATION_USER_PROMPT_TEMPLATE.format(
        original_text=original_text,
        summary=summary,
    )

    response = _call_llm(
        system_prompt=VERIFICATION_SYSTEM_PROMPT,
        user_prompt=prompt,
    )

    # Parse die Antwort
    response_lower = response.lower().strip()

    if response_lower.startswith("ok") or response_lower.startswith("verifiziert"):
        return {"ok": True, "issues": []}

    no_problem_phrases = [
        "keine probleme", "keine halluzination", "alles korrekt",
        "vollständig korrekt", "keine fehler", "keine abweichung",
        "alle aussagen sind belegt", "alle fakten sind korrekt",
    ]
    if any(phrase in response_lower for phrase in no_problem_phrases):
        return {"ok": True, "issues": []}

    issues = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if line and len(line) > 10:
            line = re.sub(r"^[\-\*\d\.]+\s*", "", line).strip()
            if line:
                issues.append(line)

    if not issues:
        issues = [response.strip()[:200]]

    return {"ok": False, "issues": issues}


def _fix_summary(original_text: str, summary: str, issues: list[str]) -> str:
    """Korrigiert eine Zusammenfassung basierend auf gefundenen Problemen."""
    issues_text = "\n".join(f"- {issue}" for issue in issues)

    prompt = f"""Die folgende Zusammenfassung enthält Fehler. Korrigiere sie.

REGELN:
- Entferne ALLE Aussagen die nicht direkt im Originaltext belegt sind
- Erfinde NICHTS dazu
- Wenn eine Information unklar ist, lass sie weg
- Behalte nur Fakten die wörtlich oder sinngemäß im Original stehen

GEFUNDENE PROBLEME:
{issues_text}

ORIGINALTEXT:
{original_text}

FEHLERHAFTE ZUSAMMENFASSUNG:
{summary}

KORRIGIERTE ZUSAMMENFASSUNG:"""

    return _call_llm(
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=prompt,
    )


def summarize_act(document_summaries: list[dict]) -> str:
    """
    Erstellt eine Klartext-Gesamtübersicht aus Einzelzusammenfassungen (Stufe 3a).
    Keine Anonymisierung - für kanzleiinternen Gebrauch.
    """
    parts = []
    for i, doc in enumerate(document_summaries, 1):
        verified_tag = "✓" if doc.get("verified", False) else "⚠"
        parts.append(
            f"### Dokument {i} [{verified_tag}]: {doc['source_file']}\n\n{doc['summary']}"
        )

    combined = "\n\n---\n\n".join(parts)

    user_prompt = f"""Hier sind {len(document_summaries)} Einzelzusammenfassungen
aus einem Rechtsakt. Erstelle daraus eine chronologische Gesamtübersicht.

WICHTIG: Verwende NUR Informationen aus den Einzelzusammenfassungen.
Erfinde KEINE zusätzlichen Fakten, Schlussfolgerungen oder Rechtsanalysen.
Verwende die echten Namen und Angaben - KEINE Anonymisierung.
Dokumente mit ⚠ sind nicht vollständig verifiziert - kennzeichne diese Info.

{combined}
"""

    response = _call_llm(
        system_prompt=ACT_SUMMARY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        label=f"Gesamtbriefing ({len(document_summaries)} Dokumente)",
    )

    return response


def anonymize_text(klartext: str) -> str:
    """
    Anonymisiert einen fertigen Klartext-Sachverhalt (Stufe 3b).
    Gibt den anonymisierten Text zurück, bereit für Cloud-LLM-Prompts.
    """
    user_prompt = ANON_USER_PROMPT_TEMPLATE.format(text=klartext)

    response = _call_llm(
        system_prompt=ANON_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        label="Anonymisierung",
    )

    return response


def _is_local_endpoint(url: str) -> bool:
    """Grobe Prüfung, ob ein Endpunkt im eigenen Netz liegt (localhost/LAN)."""
    m = re.match(r"^https?://([^/:]+)", url or "")
    if not m:
        return False
    host = m.group(1).lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return True
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host))


def extract_pii_terms(text: str) -> list[tuple[str, str]] | None:
    """Lässt ein Modell die Identifikatoren im Klartext VORSCHLAGEN
    (Namen, Firmen, Anschriften, GZ, IBAN, ...). Ersetzt wird später
    deterministisch in pseudonymizer.apply() — dieser Lauf liefert nur
    Kandidaten.

    Läuft bewusst über das ANON-Backend (Default: lokales Ollama) und NICHT
    über das Briefing-Backend: der Klartext darf diese Maschine bzw. das
    eigene Netz nicht verlassen, egal wohin NEWCASE_BACKEND zeigt.

    Returns:
        Liste (wert, kategorie) oder None, wenn das Modell kein
        verwertbares JSON geliefert hat.
    """
    if ANON_BACKEND == "openai_compat":
        base = (ANON_BASE_URL or "").strip().rstrip("/")
        if not base:
            logger.error(
                "NEWCASE_ANON_BACKEND=openai_compat, aber NEWCASE_ANON_BASE_URL "
                "fehlt — Kandidatensuche nicht möglich."
            )
            return None
        if not _is_local_endpoint(base):
            logger.warning(
                f"ACHTUNG: Der Anonymisierungs-Endpunkt {base} sieht nicht nach "
                f"localhost/LAN aus. Die Kandidatensuche schickt KLARTEXT dorthin — "
                f"für Mandantendaten nur lokale Endpunkte verwenden."
            )
        payload = {
            "model": ANON_MODEL,
            "messages": [
                {"role": "system", "content": pseudonymizer.EXTRACT_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
        }
        headers = {"Authorization": f"Bearer {ANON_API_KEY}",
                   "Content-Type": "application/json"}
        try:
            resp = requests.post(f"{base}/chat/completions", json=payload,
                                 headers=headers, timeout=OLLAMA_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            logger.error(f"Kandidatensuche fehlgeschlagen ({base}): {e}")
            return None
    else:
        base = (ANON_BASE_URL or OLLAMA_BASE_URL).rstrip("/")
        payload = {
            "model": ANON_MODEL,
            "messages": [
                {"role": "system", "content": pseudonymizer.EXTRACT_PROMPT},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "options": {"temperature": 0.0, "num_ctx": NUM_CTX},
        }
        try:
            resp = requests.post(f"{base}/api/chat", json=payload,
                                 timeout=OLLAMA_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()["message"]["content"]
        except Exception as e:  # noqa: BLE001
            logger.error(f"Kandidatensuche fehlgeschlagen ({base}, {ANON_MODEL}): {e}")
            return None

    if "<think>" in raw:
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    found = pseudonymizer.parse_extraction_json(raw)
    if found is None:
        logger.error(
            "Das Modell hat für die Kandidatensuche kein verwertbares JSON "
            "geliefert — deterministische Anonymisierung nicht möglich."
        )
    return found


def _call_ollama(system_prompt: str, user_prompt: str, label: str = "") -> str:
    """Ruft Ollama API auf und gibt die Antwort zurück.

    Args:
        label: Optionales Label für die Token-Statistik im Log (z.B. "Dok 3/11")
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 1.0,  # Google-Empfehlung für Gemma4 (Thinking Mode)
            "top_p": 0.95,
            "top_k": 64,
            "num_ctx": NUM_CTX,
        },
    }

    try:
        t_start = time.time()
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        t_elapsed = time.time() - t_start

        content = data["message"]["content"]

        # === Token-Statistik loggen ===
        prompt_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)
        total_tokens = prompt_tokens + output_tokens
        ctx_pct = (total_tokens / NUM_CTX) * 100 if total_tokens > 0 else 0
        tok_per_sec = output_tokens / t_elapsed if t_elapsed > 0 else 0

        warn = "  ⚠️ NEAR LIMIT" if ctx_pct > 90 else ""
        prefix = f"[{label}] " if label else ""
        ctx_label = f"{NUM_CTX // 1024}k" if NUM_CTX % 1024 == 0 else f"{NUM_CTX}"

        logger.info(
            f"  {prefix}📊 {prompt_tokens:,} prompt + {output_tokens:,} output "
            f"= {total_tokens:,} tokens ({ctx_pct:.1f}% von {ctx_label}) "
            f"| {tok_per_sec:.1f} tok/s | {t_elapsed:.0f}s{warn}"
        )

        # Gemma4 Thinking Mode: Entferne den internen Denkprozess aus dem Output
        if "<|channel>" in content:
            parts = content.split("<channel|>")
            content = parts[-1].strip()
        if content.startswith("<|channel>"):
            content = re.sub(r"<\|channel>thought\n.*?<channel\|>", "", content, flags=re.DOTALL).strip()

        return content

    except requests.Timeout:
        logger.error("Ollama Timeout - Dokument zu lang oder Modell zu langsam")
        return "[FEHLER: Timeout bei Zusammenfassung]"

    except requests.ConnectionError:
        logger.error("Ollama nicht erreichbar")
        return "[FEHLER: Ollama nicht erreichbar]"

    except Exception as e:
        logger.error(f"Ollama Fehler: {e}")
        return f"[FEHLER: {e}]"


def _extract_api_error(resp) -> str:
    """Holt die Klartext-Fehlermeldung aus einer Error-Response.

    Die Anbieter antworten leider unterschiedlich:
      OpenAI / LM Studio : {"error": {"message": "..."}}
      Mistral            : {"object": "error", "message": "...", "type": "..."}
      Mistral (Pydantic) : {"detail": [{"loc": [...], "msg": "..."}]}
      Anthropic          : {"type": "error", "error": {"message": "..."}}
    """
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "").strip()[:500] or "kein Response-Body"

    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if body.get("message"):
            return str(body["message"])
        detail = body.get("detail")
        if isinstance(detail, list):
            msgs = [
                f"{'.'.join(str(p) for p in d.get('loc', []))}: {d.get('msg', '')}".strip(": ")
                for d in detail
                if isinstance(d, dict)
            ]
            if msgs:
                return "; ".join(msgs)
        if isinstance(detail, str) and detail:
            return detail

    return json.dumps(body, ensure_ascii=False)[:500]


def _call_openai_compat(system_prompt: str, user_prompt: str, label: str = "") -> str:
    """Ruft eine OpenAI-kompatible Cloud-API auf (Mistral, OpenAI, Anthropic, …).

    Unterstützt jedes Backend, das `/chat/completions` als Endpoint anbietet:
        - Mistral La Plateforme (https://api.mistral.ai/v1)
        - OpenAI (https://api.openai.com/v1)
        - Anthropic (https://api.anthropic.com/v1)
        - LM Studio, vLLM, oMLX (jeweils mit eigener Base-URL)
    """
    if not NEWCASE_API_KEY or not NEWCASE_API_BASE_URL:
        logger.error("Cloud-Backend: NEWCASE_API_KEY oder NEWCASE_API_BASE_URL fehlt")
        return "[FEHLER: API-Konfiguration unvollständig]"

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "top_p": 0.95,
    }

    headers = {
        "Authorization": f"Bearer {NEWCASE_API_KEY}",
        "Content-Type": "application/json",
    }

    endpoint = f"{_api_base()}/chat/completions"

    try:
        t_start = time.time()
        resp = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        t_elapsed = time.time() - t_start

        content = data["choices"][0]["message"]["content"]

        # OpenAI-Format: usage.prompt_tokens / completion_tokens
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + output_tokens
        tok_per_sec = output_tokens / t_elapsed if t_elapsed > 0 else 0
        prefix = f"[{label}] " if label else ""

        logger.info(
            f"  {prefix}☁️  {prompt_tokens:,} prompt + {output_tokens:,} output "
            f"= {total_tokens:,} tokens "
            f"| {tok_per_sec:.1f} tok/s | {t_elapsed:.0f}s | via {_api_base()}"
        )

        # Thinking-Blöcke entfernen (manche Modelle geben sowas aus)
        if "<think>" in content:
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        return content

    except requests.Timeout:
        logger.error("Cloud-API Timeout — Modell zu langsam oder Anfrage zu groß")
        return "[FEHLER: Timeout bei Cloud-API]"

    except requests.ConnectionError as e:
        logger.error(f"Cloud-API nicht erreichbar: {e}")
        return "[FEHLER: Cloud-API nicht erreichbar]"

    except requests.HTTPError:
        err_msg = _extract_api_error(resp)
        logger.error(
            f"Cloud-API HTTP-Fehler {resp.status_code} — {err_msg}\n"
            f"    Endpoint : {endpoint}\n"
            f"    Modell   : {OLLAMA_MODEL}\n"
            f"    Request  : {len(system_prompt):,} + {len(user_prompt):,} Zeichen "
            f"(~{(len(system_prompt) + len(user_prompt)) // 4:,} Tokens)"
        )
        return f"[FEHLER: Cloud-API {resp.status_code}: {err_msg}]"

    except Exception as e:
        logger.error(f"Cloud-API Fehler: {e}")
        return f"[FEHLER: {e}]"


def _call_llm(system_prompt: str, user_prompt: str, label: str = "") -> str:
    """Backend-agnostic LLM-Call — dispatcht je nach NEWCASE_BACKEND.

    Default: Ollama (lokal). Alternativ: OpenAI-kompatible Cloud-API.
    """
    if NEWCASE_BACKEND == "openai_compat":
        return _call_openai_compat(system_prompt, user_prompt, label)
    return _call_ollama(system_prompt, user_prompt, label)
