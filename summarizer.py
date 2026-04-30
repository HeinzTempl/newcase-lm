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
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT_TEMPLATE,
    MAIL_SYSTEM_PROMPT,
    MAIL_USER_PROMPT_TEMPLATE,
    ACT_SUMMARY_SYSTEM_PROMPT,
    ANON_SYSTEM_PROMPT,
    ANON_USER_PROMPT_TEMPLATE,
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_USER_PROMPT_TEMPLATE,
    MAX_TEXT_LENGTH,
    MAX_VERIFICATION_RETRIES,
    ENABLE_VERIFICATION,
    NUM_CTX,
)


# Dateiendungen, bei denen wir den E-Mail-Spezialprompt verwenden
MAIL_EXTENSIONS = {".msg", ".eml"}

logger = logging.getLogger(__name__)


def check_ollama_available() -> bool:
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
    summary = _call_ollama(
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

    response = _call_ollama(
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

    return _call_ollama(
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

    response = _call_ollama(
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

    response = _call_ollama(
        system_prompt=ANON_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        label="Anonymisierung",
    )

    return response


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
            timeout=600,  # 10 Min Timeout – Thinking Mode braucht länger
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
