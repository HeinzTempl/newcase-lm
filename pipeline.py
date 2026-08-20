#!/usr/bin/env python3
"""
Kanzlei Pipeline v2 - Klartext-First
======================================
Verarbeitet Dokumente aus einem Eingangsordner:
1. Text-Extraktion (PDF, DOCX, MSG, etc. → Markdown)
2. Zusammenfassung im KLARTEXT per lokalem LLM (Ollama)
3a. Gesamtübersicht im Klartext (Inhouse-Dokument)
3b. Anonymisierte Gesamtübersicht (Cloud-Prompt)

Verwendung:
    python pipeline.py                          # Interaktive Akten-Auswahl, dann verarbeiten
    python pipeline.py --case stadler           # Akte anhand Name/Substring auswählen
    python pipeline.py --new-case mietsache     # Neue Akte anlegen
    python pipeline.py --list-cases             # Vorhandene Akten auflisten
    python pipeline.py --extract-only           # Nur Text extrahieren, kein LLM
    python pipeline.py --file doc.pdf           # Nur eine bestimmte Datei
    python pipeline.py --skip-anon              # Nur Klartext, keine Anonymisierung

Heinz Kanzlei-Pipeline v2.0
"""

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from datetime import datetime

import case_layer
import config as cfg
import pseudonymizer
from config import (
    SUPPORTED_EXTENSIONS, ENABLE_REDACTION_CHECK,
    ENABLE_VERIFICATION,
)
from extractor import extract_file, save_extracted_text
from summarizer import (
    check_ollama_available, summarize_document,
    summarize_act, anonymize_text, extract_pii_terms,
)
from docx_export import export_klartext_docx, export_anon_docx

# === Logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# === Cache-Funktionen ===

def _file_hash(filepath: Path) -> str:
    """Berechnet SHA-256 Hash einer Datei."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_cache(cache_dir: Path) -> dict:
    """Lädt den Cache aus der JSON-Datei."""
    cache_file = cache_dir / "pipeline_cache.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Cache-Datei beschädigt, starte frisch")
    return {}


def _save_cache(cache_dir: Path, cache: dict):
    """Speichert den Cache als JSON-Datei."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "pipeline_cache.json"
    cache_file.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_cached_summary(cache: dict, filepath: Path) -> str | None:
    """Gibt die gecachte Zusammenfassung zurück, wenn der Hash übereinstimmt."""
    key = filepath.name
    if key in cache:
        current_hash = _file_hash(filepath)
        if cache[key].get("hash") == current_hash:
            return cache[key].get("summary")
    return None


def _update_cache(cache: dict, filepath: Path, summary: str):
    """Aktualisiert den Cache-Eintrag für eine Datei."""
    cache[filepath.name] = {
        "hash": _file_hash(filepath),
        "summary": summary,
        "timestamp": datetime.now().isoformat(),
    }


def discover_files(input_dir: Path) -> list[Path]:
    """Findet alle unterstützten Dateien im Eingangsordner."""
    files = []
    for f in sorted(input_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(f)
        elif f.is_file() and not f.name.startswith("."):
            logger.warning(f"  Übersprungen (nicht unterstützt): {f.name}")
    return files


def _wait_for_documents(input_dir: Path, files: list[Path]) -> list[Path]:
    """
    Falls keine Dateien gefunden: interaktiv warten, bis der User welche
    in den Ordner gelegt hat. Erspart Heinz den Neustart nach 'neue Akte anlegen'.

    Gibt die (eventuell nach Wartephase neu eingescannte) Dateiliste zurück.
    Bei Abbruch (Q) → sys.exit(0).
    """
    if files:
        return files

    while True:
        print()
        print(f"  Keine Dokumente gefunden in:")
        print(f"    {input_dir}")
        print()
        print(f"  Lege die Eingangsdateien (PDF, MSG, EML, DOCX, …) jetzt in den Ordner.")
        print(f"  Du kannst den Ordner z.B. mit folgendem Befehl im Finder öffnen:")
        print(f"    open \"{input_dir}\"")
        print()
        print(f"  [Enter] Erneut scannen   |   [Q] Abbrechen")
        try:
            choice = input("  Eingabe: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if choice in ("q", "quit", "exit"):
            logger.info("Abgebrochen — keine Dokumente verarbeitet.")
            sys.exit(0)
        # leer (Enter) oder irgendwas anderes → nochmal scannen
        files = discover_files(input_dir)
        if files:
            logger.info(f"  ✓ {len(files)} Dokument(e) gefunden, Pipeline läuft weiter.")
            return files
        # else: Schleife dreht weiter


def run_pipeline(
    case: case_layer.Case,
    extract_only: bool = False,
    single_file: Path = None,
    skip_anon: bool = False,
):
    """Hauptpipeline v2: Extraktion → Klartext-Zusammenfassung → Anonymisierung.

    Alle Pfade kommen aus dem übergebenen Case (Multi-Case-Layer).
    """

    input_dir = case.input_dir
    output_dir = case.output_dir
    extracted_dir = case.extracted_dir
    cache_dir = case.cache_dir

    # Probleme, die am Ende als Fehler gemeldet werden (Exit-Code != 0)
    problems: list[str] = []

    logger.info("=" * 60)
    logger.info("KANZLEI-PIPELINE v2 (Klartext-First)")
    logger.info(f"  Akte: {case.name}")
    logger.info("=" * 60)

    # Ordner vorbereiten
    case.ensure_dirs()

    # Dateien finden
    if single_file:
        if not single_file.exists():
            logger.error(f"Datei nicht gefunden: {single_file}")
            sys.exit(1)
        files = [single_file]
    else:
        if not input_dir.exists():
            input_dir.mkdir(parents=True, exist_ok=True)
        files = discover_files(input_dir)

        # Wenn leer: User-friendly Wartemodus statt sofort abbrechen
        files = _wait_for_documents(input_dir, files)

    logger.info(f"Gefunden: {len(files)} Dokument(e)")
    for f in files:
        logger.info(f"  → {f.name}")

    # ========================================
    # STUFE 1: Text-Extraktion
    # ========================================
    logger.info("")
    logger.info("-" * 40)
    logger.info("STUFE 1: Text-Extraktion")
    logger.info("-" * 40)

    extracted_docs = []

    for filepath in files:
        logger.info(f"Extrahiere: {filepath.name}")
        result = extract_file(filepath)

        if result is None:
            continue

        # Bei E-Mails (MSG/EML): Anhänge extrahieren und zusammenführen
        if result.get("attachments"):
            logger.info(f"  → {len(result['attachments'])} Anhänge gefunden")
            attachment_texts = []
            attachment_names = []

            for att in result["attachments"]:
                if att.suffix.lower() in SUPPORTED_EXTENSIONS:
                    logger.info(f"    Extrahiere Anhang: {att.name}")
                    att_result = extract_file(att)
                    if att_result and att_result["extracted_text"]:
                        attachment_texts.append(
                            f"\n\n---\n\n### Anhang: {att.name}\n\n"
                            f"{att_result['extracted_text']}"
                        )
                        attachment_names.append(att.name)
                else:
                    logger.info(f"    Übersprungen (nicht unterstützt): {att.name}")

            if attachment_texts:
                result["extracted_text"] += "\n".join(attachment_texts)
                result["attachment_names"] = attachment_names
                logger.info(f"  → E-Mail + {len(attachment_texts)} Anhänge zusammengeführt")

        # Extrahierten Text speichern
        md_path = save_extracted_text(result, extracted_dir)
        logger.info(f"  → Gespeichert: {md_path.name}")

        extracted_docs.append(result)

    logger.info(f"\nExtraktion abgeschlossen: {len(extracted_docs)} Dokumente")

    if extract_only:
        logger.info("--extract-only: Überspringe LLM-Zusammenfassung")
        logger.info(f"Extrahierte Texte liegen in: {extracted_dir}")
        return

    # ========================================
    # STUFE 2: Einzelzusammenfassungen KLARTEXT (LLM)
    # ========================================
    logger.info("")
    logger.info("-" * 40)
    logger.info("STUFE 2: Einzelzusammenfassungen KLARTEXT (LLM)")
    logger.info("-" * 40)

    if not check_ollama_available():
        logger.error("Ollama nicht verfügbar - Zusammenfassung übersprungen")
        logger.info(f"Extrahierte Texte liegen in: {extracted_dir}")
        sys.exit(1)

    # Cache laden
    cache = _load_cache(cache_dir)
    cache_hits = 0

    summaries = []
    for doc in extracted_docs:
        source_file = doc["source_file"]
        source_path = Path(source_file) if Path(source_file).exists() else input_dir / source_file

        # Cache prüfen
        cached = None
        if source_path.exists():
            cached = _get_cached_summary(cache, source_path)

        if cached:
            logger.info(f"Cache-Treffer: {source_file}")
            cache_hits += 1
            summaries.append({
                "source_file": source_file,
                "summary": cached,
                "verified": True,
            })
            continue

        # Neu zusammenfassen
        logger.info(f"Fasse zusammen (Klartext): {source_file}")
        result = summarize_document(doc)
        summaries.append({
            "source_file": source_file,
            "summary": result["summary"],
            "verified": result["verified"],
        })

        # Status anzeigen
        if result["verified"]:
            logger.info(f"  ✓ Verifiziert")
        else:
            logger.warning(f"  ⚠ Nicht vollständig verifiziert")
            for issue in result.get("issues", []):
                logger.warning(f"    - {issue}")

        # Cache aktualisieren
        if source_path.exists() and result["verified"]:
            _update_cache(cache, source_path, result["summary"])

        # Klartext-Einzelzusammenfassung speichern
        source_stem = Path(source_file).stem
        if ENABLE_VERIFICATION:
            verified_tag = "✓ VERIFIZIERT" if result["verified"] else "⚠ NICHT VOLLSTÄNDIG VERIFIZIERT"
            status_line = f"**Status:** {verified_tag}\n"
        else:
            status_line = ""
        summary_path = output_dir / f"{source_stem}_klartext.md"
        summary_path.write_text(
            f"# Zusammenfassung (Klartext): {source_file}\n"
            f"{status_line}"
            f"**VERTRAULICH - NUR FÜR KANZLEIINTERNEN GEBRAUCH**\n\n"
            f"{result['summary']}",
            encoding="utf-8",
        )
        logger.info(f"  → {summary_path.name}")

    # Cache speichern
    _save_cache(cache_dir, cache)
    if cache_hits:
        logger.info(f"\nCache: {cache_hits} von {len(extracted_docs)} aus Cache geladen")
    logger.info(f"Klartext-Zusammenfassungen abgeschlossen: {len(summaries)} Dokumente")

    # ========================================
    # STUFE 3a: Gesamtübersicht KLARTEXT (Inhouse)
    # ========================================
    if len(summaries) > 1:
        logger.info("")
        logger.info("-" * 40)
        logger.info("STUFE 3a: Gesamtübersicht KLARTEXT (Inhouse)")
        logger.info("-" * 40)

        act_summary_klartext = summarize_act(summaries)

        # Dokumentenübersicht erstellen (mit echten Dokumenttypen aus LLM)
        doc_overview = _build_doc_overview(extracted_docs, summaries)

        # Klartext-Gesamtübersicht speichern
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        klartext_path = output_dir / f"KLARTEXT_{timestamp}.md"
        timestamp_display = datetime.now().strftime("%d.%m.%Y %H:%M")
        klartext_path.write_text(
            f"# Gesamtübersicht Akt (KLARTEXT)\n\n"
            f"*Erstellt: {timestamp_display}*\n"
            f"**VERTRAULICH - NUR FÜR KANZLEIINTERNEN GEBRAUCH**\n\n"
            f"---\n\n{doc_overview}\n\n---\n\n{act_summary_klartext}",
            encoding="utf-8",
        )
        logger.info(f"  → {klartext_path.name}")

        # DOCX-Export Klartext
        klartext_docx_path = output_dir / f"KLARTEXT_{timestamp}.docx"
        export_klartext_docx(
            act_summary=act_summary_klartext,
            doc_overview=doc_overview,
            output_path=klartext_docx_path,
            timestamp=timestamp_display,
        )

        # ========================================
        # STUFE 3b: Anonymisierte Version (Cloud-Prompt)
        # ========================================
        if not skip_anon:
            logger.info("")
            logger.info("-" * 40)
            logger.info("STUFE 3b: Anonymisierung für Cloud-Prompt")
            logger.info("-" * 40)

            # Anonymisieren (Modus: NEWCASE_ANON_MODE)
            anon_summary, mapping_table, anon_problems = _run_anonymization(
                act_summary_klartext, case.name
            )
            problems.extend(anon_problems)

            # Dokumentenübersicht (identisch zur Klartext-Version, enthält keine PII)
            anon_doc_overview = doc_overview

            if anon_summary is not None:
                banner = ""
                if anon_problems:
                    banner = ("> ⚠ **NICHT FREIGEGEBEN — NICHT NACH AUSSEN GEBEN**\n"
                              + "".join(f"> - {p}\n" for p in anon_problems) + "\n")
                anon_path = output_dir / f"ANON_{timestamp}.md"
                anon_path.write_text(
                    f"# Gesamtübersicht Akt (ANONYMISIERT)\n\n"
                    f"*Erstellt: {timestamp_display}*\n"
                    f"*Anonymisiert für Cloud-LLM-Nutzung*\n\n"
                    f"{banner}"
                    f"---\n\n{anon_doc_overview}\n\n---\n\n{anon_summary}",
                    encoding="utf-8",
                )
                logger.info(f"  → {anon_path.name}")

            # Zuordnungstabelle NUR an das Klartext-Dokument anhängen —
            # niemals an die ANON-Dateien, die nach außen gehen.
            if mapping_table:
                logger.info(f"  → Zuordnungstabelle wird an Klartext angehängt")
                # MD-Datei ergänzen
                with open(klartext_path, "a", encoding="utf-8") as f:
                    f.write(f"\n\n---\n\n## Zuordnung Klartext → Anonymisiert\n\n{mapping_table}")
                # DOCX neu exportieren mit Zuordnungstabelle
                export_klartext_docx(
                    act_summary=act_summary_klartext,
                    doc_overview=doc_overview,
                    output_path=klartext_docx_path,
                    timestamp=timestamp_display,
                    mapping_table=mapping_table,
                )

            # DOCX-Export Anonymisiert — nur wenn die Anonymisierung sauber war
            if anon_summary is not None and not anon_problems:
                anon_docx_path = output_dir / f"ANON_{timestamp}.docx"
                export_anon_docx(
                    anon_summary=anon_summary,
                    doc_overview=anon_doc_overview,
                    output_path=anon_docx_path,
                    timestamp=timestamp_display,
                )
            elif anon_problems:
                logger.error("  ANON-DOCX übersprungen — Anonymisierung nicht freigegeben (Details am Ende).")
        else:
            logger.info("\n--skip-anon: Anonymisierung übersprungen")

    elif len(summaries) == 1:
        # Einzeldokument: Klartext-DOCX + Dokumentenübersicht + Anonymisierung
        logger.info("")
        logger.info("-" * 40)
        logger.info("STUFE 3: Einzeldokument-Output")
        logger.info("-" * 40)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        timestamp_display_single = datetime.now().strftime("%d.%m.%Y %H:%M")
        source_stem = Path(summaries[0]["source_file"]).stem

        # Dokumentenübersicht auch für Einzeldokument
        doc_overview = _build_doc_overview(extracted_docs, summaries)

        # Klartext-DOCX exportieren
        klartext_docx_path = output_dir / f"{source_stem}_klartext.docx"
        export_klartext_docx(
            act_summary=summaries[0]["summary"],
            doc_overview=doc_overview,
            output_path=klartext_docx_path,
            timestamp=timestamp_display_single,
        )

        if not skip_anon:
            logger.info("")
            logger.info("-" * 40)
            logger.info("STUFE 3b: Anonymisierung für Cloud-Prompt")
            logger.info("-" * 40)

            anon_summary, mapping_table, anon_problems = _run_anonymization(
                summaries[0]["summary"], case.name
            )
            problems.extend(anon_problems)

            if anon_summary is not None:
                banner = ""
                if anon_problems:
                    banner = ("> ⚠ **NICHT FREIGEGEBEN — NICHT NACH AUSSEN GEBEN**\n"
                              + "".join(f"> - {p}\n" for p in anon_problems) + "\n")
                anon_path = output_dir / f"{source_stem}_anon.md"
                anon_path.write_text(
                    f"# Zusammenfassung (ANONYMISIERT): {source_stem}\n\n"
                    f"*Anonymisiert für Cloud-LLM-Nutzung*\n\n"
                    f"{banner}"
                    f"{anon_summary}",
                    encoding="utf-8",
                )
                logger.info(f"  → {anon_path.name}")

            # Zuordnungstabelle NUR an den Klartext (MD + DOCX) —
            # niemals an die ANON-Dateien, die nach außen gehen.
            if mapping_table:
                logger.info(f"  → Zuordnungstabelle wird an Klartext angehängt")
                klartext_md_path = output_dir / f"{source_stem}_klartext.md"
                if klartext_md_path.exists():
                    with open(klartext_md_path, "a", encoding="utf-8") as f:
                        f.write(f"\n\n---\n\n## Zuordnung Klartext → Anonymisiert\n\n{mapping_table}")
                # DOCX neu exportieren mit Zuordnungstabelle
                export_klartext_docx(
                    act_summary=summaries[0]["summary"],
                    doc_overview=doc_overview,
                    output_path=klartext_docx_path,
                    timestamp=timestamp_display_single,
                    mapping_table=mapping_table,
                )

            # DOCX-Export Anonymisiert — nur wenn die Anonymisierung sauber war
            if anon_summary is not None and not anon_problems:
                anon_docx_path = output_dir / f"{source_stem}_anon.docx"
                export_anon_docx(
                    anon_summary=anon_summary,
                    doc_overview=doc_overview,
                    output_path=anon_docx_path,
                    timestamp=timestamp_display_single,
                )
            elif anon_problems:
                logger.error("  ANON-DOCX übersprungen — Anonymisierung nicht freigegeben (Details am Ende).")

    # ========================================
    # Fertig
    # ========================================
    logger.info("")
    logger.info("=" * 60)
    logger.info("PIPELINE v2 ABGESCHLOSSEN")
    logger.info(f"  Akte:                    {case.name}")
    logger.info(f"  Extrahierte Texte:       {extracted_dir}")
    logger.info(f"  Klartext-Zusammenfass.:   {output_dir}")
    if not skip_anon:
        logger.info(f"  Anonymisierte Version:   {output_dir}")
    logger.info("=" * 60)

    if problems:
        logger.error("")
        logger.error("!" * 60)
        logger.error("ANONYMISIERUNG NICHT FREIGEGEBEN — bitte prüfen:")
        for p in problems:
            logger.error(f"  - {p}")
        logger.error("!" * 60)
        sys.exit(2)


def _build_doc_overview(extracted_docs: list[dict], summaries: list[dict]) -> str:
    """Erstellt die Dokumentenübersicht-Tabelle."""
    doc_overview_lines = [
        f"## Dokumentenübersicht ({len(extracted_docs)} Dokumente)\n",
        "| Nr. | Dokument | Dokumentdatum |",
        "|-----|----------|---------------|",
    ]
    for i, doc in enumerate(extracted_docs, 1):
        # Dokumenttyp aus der LLM-Zusammenfassung extrahieren
        doc_label = _extract_doc_type_from_summary(summaries[i-1]["summary"]) if i <= len(summaries) else None

        if not doc_label:
            fallback_map = {
                ".pdf": "PDF-Dokument",
                ".docx": "Word-Dokument",
                ".doc": "Word-Dokument",
                ".msg": "E-Mail-Korrespondenz",
                ".eml": "E-Mail-Korrespondenz",
                ".txt": "Textdokument",
                ".rtf": "Textdokument",
            }
            doc_label = fallback_map.get(doc.get("file_type", ""), "Dokument")

        # Dokumentdatum aus Summary extrahieren
        doc_date = _extract_doc_date_from_summary(summaries[i-1]["summary"]) if i <= len(summaries) else None
        date_str = doc_date if doc_date else "–"

        # Anhänge bei E-Mails auflisten
        att_names = doc.get("attachment_names", [])
        if att_names:
            doc_overview_lines.append(
                f"| {i} | {doc_label} ({len(att_names)} Anhänge) | {date_str} |"
            )
            att_type_map = {
                ".pdf": "PDF-Dokument",
                ".docx": "Word-Dokument",
                ".doc": "Word-Dokument",
                ".xlsx": "Excel-Tabelle",
                ".txt": "Textdokument",
                ".rtf": "Textdokument",
            }
            for j, att_name in enumerate(att_names, 1):
                att_ext = Path(att_name).suffix.lower()
                att_type = att_type_map.get(att_ext, f"Anhang ({att_ext})")
                doc_overview_lines.append(f"|   | ↳ {att_type} ({j}) | |")
        else:
            doc_overview_lines.append(f"| {i} | {doc_label} | {date_str} |")

    return "\n".join(doc_overview_lines)


def _split_mapping_table(anon_raw: str) -> tuple[str, str | None]:
    """Trennt die Zuordnungstabelle vom anonymisierten Text.

    Returns:
        (anon_text, mapping_table) – mapping_table ist None wenn nicht gefunden.
    """
    marker = "---ZUORDNUNG---"
    if marker in anon_raw:
        parts = anon_raw.split(marker, 1)
        anon_text = parts[0].strip()
        mapping_table = parts[1].strip()
        return anon_text, mapping_table
    return anon_raw.strip(), None


def _mapping_table_md(map_id: str) -> str | None:
    """Zuordnungstabelle (Markdown) aus der JSON-Map — für das Klartext-Dokument."""
    mapping, _alias = pseudonymizer.load_map(
        pseudonymizer.map_path(map_id, cfg.PSEUDONYM_DIR)
    )
    if not mapping:
        return None
    lines = ["| Originalbezeichnung | Platzhalter |",
             "|---------------------|-------------|"]
    for ph, val in sorted(mapping.items()):
        lines.append(f"| {val} | {ph} |")
    return "\n".join(lines)


def _run_anonymization(text: str, map_id: str) -> tuple[str | None, str | None, list[str]]:
    """Stufe 3b in allen Modi (NEWCASE_ANON_MODE=llm|deterministic|both).

    Returns:
        (anon_text, zuordnungstabelle_fuer_klartext, probleme)
        anon_text ist None, wenn gar kein verwertbares Ergebnis entstand.
        Ein nicht-leeres 'probleme' heißt: Ergebnis NICHT nach außen geben.
    """
    mode = (cfg.ANON_MODE or "llm").lower()
    problems: list[str] = []
    if mode not in ("llm", "deterministic", "both"):
        return None, None, [
            f"Unbekannter NEWCASE_ANON_MODE '{mode}' — erlaubt: llm|deterministic|both."
        ]
    logger.info(f"  Anonymisierungs-Modus: {mode}")

    anon_text = None
    mapping_table = None

    # --- LLM-Anonymisierung (Modus llm und both) ---
    if mode in ("llm", "both"):
        anon_raw = anonymize_text(text)
        if anon_raw.startswith("[FEHLER"):
            return None, None, [f"LLM-Anonymisierung fehlgeschlagen: {anon_raw}"]
        anon_text, mapping_table = _split_mapping_table(anon_raw)
        if mapping_table:
            added, skipped = pseudonymizer.merge_llm_mapping_table(
                mapping_table, map_id, cfg.PSEUDONYM_DIR
            )
            note = f"  Zuordnungstabelle: {added} Einträge in die JSON-Map übernommen"
            if skipped:
                note += (f", {skipped} ohne [ECKIGE]-Platzhalter übersprungen "
                         f"(für diese gibt es keinen automatischen Rückweg)")
            logger.info(note)
        else:
            problems.append(
                "Das Modell hat KEINE Zuordnungstabelle geliefert "
                "(Marker ---ZUORDNUNG--- fehlt) — ohne Tabelle gibt es keinen "
                "Rückweg (depseudo.py). Lauf wiederholen oder "
                "NEWCASE_ANON_MODE=deterministic verwenden."
            )

    # --- Deterministische Ersetzung (Modus deterministic und both) ---
    if mode in ("deterministic", "both"):
        src = anon_text if anon_text is not None else text
        found = extract_pii_terms(src)
        if found is None:
            problems.append(
                "Kandidatensuche für die deterministische Anonymisierung "
                "fehlgeschlagen (siehe Log) — keine Ersetzung durchgeführt."
            )
            if mode == "deterministic":
                return None, None, problems
        else:
            result = pseudonymizer.pseudonymize_text(
                src, map_id, cfg.PSEUDONYM_DIR, found=found
            )
            anon_text = result.text
            logger.info(
                f"  Deterministisch ersetzt: {sum(result.hits.values())} Stellen "
                f"({result.new_entries} neue Einträge, gesamt {result.total_entries}) "
                f"| Tabelle: {result.map_path}"
            )

    # --- Endkontrolle: steht ein bekannter Klarwert noch im Ergebnis? ---
    if anon_text is not None:
        residuals = pseudonymizer.verify_no_cleartext(
            anon_text, map_id, cfg.PSEUDONYM_DIR
        )
        if residuals:
            problems.append(
                "Bekannte Klarwerte stehen noch im anonymisierten Text "
                f"({', '.join(residuals)}) — NICHT nach außen geben, "
                "Lauf wiederholen."
            )

    # Tabelle für das Klartext-Dokument: im deterministischen Modus aus der
    # JSON-Map erzeugen (das LLM liefert dort keine).
    if mapping_table is None:
        mapping_table = _mapping_table_md(map_id)

    return anon_text, mapping_table, problems


def _extract_doc_type_from_summary(summary_text: str) -> str | None:
    """Extrahiert den Dokumenttyp aus der LLM-Zusammenfassung."""
    match = re.search(r"\*\*Dokumenttyp:\*\*\s*(.+?)(?:\n|$)", summary_text)
    if match:
        doc_type = match.group(1).strip().rstrip(".")
        doc_type = doc_type.replace("**", "").strip()
        if doc_type and len(doc_type) < 80:
            return doc_type
    return None


def _extract_doc_date_from_summary(summary_text: str) -> str | None:
    """Extrahiert das Dokumentdatum aus der LLM-Zusammenfassung."""
    for pattern in [
        r"\*\*Dokumentdatum:\*\*\s*(.+?)(?:\n|$)",
        r"\*\*Datum:\*\*\s*(.+?)(?:\n|$)",
    ]:
        match = re.search(pattern, summary_text)
        if match:
            date_str = match.group(1).strip().rstrip(".")
            date_str = date_str.replace("**", "").strip()
            if date_str and date_str.lower() not in ["nicht erkennbar", "–", "-", "unklar", "weglassen", ""]:
                return date_str
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Kanzlei-Pipeline v2: Klartext-First mit optionaler Anonymisierung"
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Nur Text extrahieren, keine LLM-Zusammenfassung",
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Nur eine bestimmte Datei verarbeiten",
    )
    parser.add_argument(
        "--skip-anon",
        action="store_true",
        help="Nur Klartext-Output, keine Anonymisierung (Stufe 3b überspringen)",
    )
    parser.add_argument(
        "--anon-mode",
        choices=["llm", "deterministic", "both"],
        help="Anonymisierungs-Modus für diesen Lauf (überschreibt NEWCASE_ANON_MODE)",
    )
    # Case-Layer-Argumente (--case, --new-case, --list-cases)
    case_layer.add_case_args(parser)

    args = parser.parse_args()

    # Hinweis bei Legacy-Daten (alter Single-Case-Modus)
    if case_layer.detect_legacy_data() and not (args.case or args.new_case or args.list_cases):
        case_layer.print_migration_hint()

    # Akte auswählen oder neu anlegen
    selected_case = case_layer.select_or_create_case_from_args(args)

    # Pfade in config.py auf den Case patchen (für Module, die config.X lesen)
    case_layer.apply_case_to_config(selected_case)

    # Anonymisierungs-Modus per CLI überschreiben
    if args.anon_mode:
        cfg.ANON_MODE = args.anon_mode

    run_pipeline(
        case=selected_case,
        extract_only=args.extract_only,
        single_file=args.file,
        skip_anon=args.skip_anon,
    )
