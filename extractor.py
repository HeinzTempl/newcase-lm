"""
Kanzlei Pipeline - Dokumenten-Extraktor
========================================
Extrahiert Text aus verschiedenen Dokumenttypen und wandelt ihn in Markdown um.
"""

import json
import email
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def extract_file(filepath: Path) -> dict:
    """
    Extrahiert Text aus einer Datei und gibt ein dict zurück:
    {
        "source_file": str,
        "file_type": str,
        "extracted_text": str,
        "metadata": dict,  # Datum, Betreff, etc.
        "attachments": list[Path],  # Bei MSG: extrahierte Anhänge
    }
    """
    ext = filepath.suffix.lower()

    extractors = {
        ".pdf": extract_pdf,
        ".docx": extract_docx,
        ".doc": extract_doc,
        ".msg": extract_msg,
        ".eml": extract_eml,
        ".txt": extract_txt,
        ".rtf": extract_rtf,
    }

    extractor = extractors.get(ext)
    if not extractor:
        logger.warning(f"Kein Extraktor für {ext}: {filepath}")
        return None

    try:
        result = extractor(filepath)
        result["source_file"] = str(filepath.name)
        result["file_type"] = ext
        return result
    except Exception as e:
        logger.error(f"Fehler bei Extraktion von {filepath}: {e}")
        return {
            "source_file": str(filepath.name),
            "file_type": ext,
            "extracted_text": f"[FEHLER BEI EXTRAKTION: {e}]",
            "metadata": {"error": str(e)},
            "attachments": [],
        }


# === PDF-Extraktion (via Docling) ===

def extract_pdf(filepath: Path) -> dict:
    """Extrahiert Text aus PDF via Docling → Markdown. Mit OCR-Fallback für Bild-PDFs."""

    # Schritt 1: Prüfe ob PDF einen Textlayer hat
    has_text = _pdf_has_text_layer(filepath)
    ocr_used = False
    text = ""

    if has_text:
        # Normaler Pfad: Text-PDF
        try:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(str(filepath))
            text = result.document.export_to_markdown()
        except ImportError:
            logger.info("Docling nicht verfügbar, verwende PyMuPDF als Fallback")
            text = _extract_pdf_pymupdf(filepath)
    else:
        # Bild-PDF: OCR nötig
        logger.warning(f"  ⚠ Kein Textlayer erkannt – versuche OCR: {filepath.name}")
        text, ocr_used = _extract_pdf_ocr(filepath)

    metadata = _get_file_metadata(filepath)
    if ocr_used:
        metadata["ocr"] = True
        metadata["hinweis"] = "Text wurde per OCR extrahiert – Qualität ggf. prüfen"
        text = f"[⚠ OCR-EXTRAKTION – Qualität bitte prüfen]\n\n{text}"

    return {
        "extracted_text": text,
        "metadata": metadata,
        "attachments": [],
    }


def _pdf_has_text_layer(filepath: Path) -> bool:
    """Prüft ob ein PDF extrahierbaren Text enthält (vs. reines Bild-PDF)."""
    import fitz
    doc = fitz.open(str(filepath))
    total_text = ""
    # Prüfe die ersten 3 Seiten (oder alle, wenn weniger)
    for i, page in enumerate(doc):
        if i >= 3:
            break
        total_text += page.get_text("text").strip()
    doc.close()
    # Wenn weniger als 50 Zeichen auf 3 Seiten → wahrscheinlich Bild-PDF
    return len(total_text) > 50


def _extract_pdf_pymupdf(filepath: Path) -> str:
    """Fallback PDF-Extraktion mit PyMuPDF."""
    import fitz  # PyMuPDF
    doc = fitz.open(str(filepath))
    pages = []
    for page in doc:
        pages.append(page.get_text("text"))
    doc.close()
    return "\n\n---\n\n".join(pages)


def _extract_pdf_ocr(filepath: Path) -> tuple[str, bool]:
    """OCR-Extraktion für Bild-PDFs. Gibt (text, ocr_verwendet) zurück."""
    import subprocess
    import fitz

    # Versuch 1: Docling mit OCR-Pipeline (wenn installiert)
    try:
        from docling.document_converter import DocumentConverter
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        pipeline_options = PdfPipelineOptions(do_ocr=True)
        converter = DocumentConverter(
            format_options={
                "pdf": PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(str(filepath))
        text = result.document.export_to_markdown()
        if text.strip():
            logger.info("  OCR via Docling erfolgreich")
            return text, True
    except (ImportError, Exception) as e:
        logger.info(f"  Docling-OCR nicht verfügbar: {e}")

    # Versuch 2: Tesseract über PyMuPDF (Bilder extrahieren → Tesseract)
    try:
        subprocess.run(
            ["tesseract", "--version"],
            capture_output=True, timeout=5
        )
    except FileNotFoundError:
        logger.warning(
            "  Tesseract nicht installiert. Installiere mit: brew install tesseract tesseract-lang"
        )
        return (
            "[BILD-PDF: Kein Text extrahierbar. "
            "Bitte durch ABBY FineReader oder Tesseract (brew install tesseract tesseract-lang) verarbeiten.]",
            False,
        )

    # Tesseract ist da – Seiten als Bilder extrahieren und OCR laufen lassen
    doc = fitz.open(str(filepath))
    pages_text = []

    for i, page in enumerate(doc):
        # Seite als hochauflösendes Bild rendern
        pix = page.get_pixmap(dpi=300)
        img_path = filepath.parent / f"_ocr_temp_{filepath.stem}_p{i}.png"
        pix.save(str(img_path))

        # Tesseract OCR (deutsch + englisch)
        try:
            result = subprocess.run(
                ["tesseract", str(img_path), "stdout", "-l", "deu+eng"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                pages_text.append(result.stdout.strip())
        except subprocess.TimeoutExpired:
            pages_text.append(f"[OCR Timeout auf Seite {i + 1}]")
        finally:
            # Temp-Bild aufräumen
            img_path.unlink(missing_ok=True)

    doc.close()

    if pages_text:
        logger.info(f"  OCR via Tesseract erfolgreich: {len(pages_text)} Seiten")
        return "\n\n---\n\n".join(pages_text), True
    else:
        return (
            "[BILD-PDF: OCR hat keinen Text ergeben. "
            "Bitte durch ABBY FineReader verarbeiten.]",
            False,
        )


# === Word-Extraktion ===

def extract_docx(filepath: Path) -> dict:
    """Extrahiert Text aus DOCX via Docling oder python-docx."""
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(str(filepath))
        text = result.document.export_to_markdown()
    except ImportError:
        logger.info("Docling nicht verfügbar, verwende python-docx als Fallback")
        text = _extract_docx_fallback(filepath)

    return {
        "extracted_text": text,
        "metadata": _get_file_metadata(filepath),
        "attachments": [],
    }


def _extract_docx_fallback(filepath: Path) -> str:
    """Fallback DOCX-Extraktion mit python-docx."""
    from docx import Document
    doc = Document(str(filepath))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_doc(filepath: Path) -> dict:
    """Extrahiert Text aus älteren .doc Dateien via antiword oder textutil (macOS)."""
    import subprocess

    # macOS: textutil kann .doc in .txt konvertieren
    try:
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return {
                "extracted_text": result.stdout,
                "metadata": _get_file_metadata(filepath),
                "attachments": [],
            }
    except FileNotFoundError:
        pass

    # Fallback: antiword
    try:
        result = subprocess.run(
            ["antiword", str(filepath)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return {
                "extracted_text": result.stdout,
                "metadata": _get_file_metadata(filepath),
                "attachments": [],
            }
    except FileNotFoundError:
        pass

    return {
        "extracted_text": "[.doc-Datei konnte nicht extrahiert werden - bitte als .docx speichern]",
        "metadata": _get_file_metadata(filepath),
        "attachments": [],
    }


# === Outlook MSG-Extraktion ===

def extract_msg(filepath: Path) -> dict:
    """Extrahiert E-Mail-Text und Anhänge aus MSG-Dateien."""
    import extract_msg as em

    msg = em.Message(str(filepath))

    # E-Mail-Metadaten
    metadata = {
        "subject": msg.subject or "",
        "sender": msg.sender or "",
        "date": msg.date or "",
        "to": msg.to or "",
    }

    # E-Mail-Body
    body = msg.body or ""

    # Header-Info für Kontext
    header = f"""## E-Mail
- **Betreff:** {metadata['subject']}
- **Datum:** {metadata['date']}
"""

    # Anhänge extrahieren
    attachments = []
    attachment_dir = filepath.parent / f"_attachments_{filepath.stem}"

    if msg.attachments:
        attachment_dir.mkdir(exist_ok=True)
        for att in msg.attachments:
            att_path = attachment_dir / att.longFilename
            att.save(customPath=str(attachment_dir))
            if att_path.exists():
                attachments.append(att_path)
                logger.info(f"  Anhang extrahiert: {att.longFilename}")

    msg.close()

    return {
        "extracted_text": header + "\n" + body,
        "metadata": metadata,
        "attachments": attachments,
    }


# === EML-Extraktion ===

def extract_eml(filepath: Path) -> dict:
    """Extrahiert Text aus Standard-E-Mail-Dateien (.eml)."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        msg = email.message_from_file(f)

    metadata = {
        "subject": msg.get("Subject", ""),
        "sender": msg.get("From", ""),
        "date": msg.get("Date", ""),
        "to": msg.get("To", ""),
    }

    # Body extrahieren
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")

    header = f"""## E-Mail
- **Betreff:** {metadata['subject']}
- **Datum:** {metadata['date']}
"""

    return {
        "extracted_text": header + "\n" + body,
        "metadata": metadata,
        "attachments": [],  # EML-Anhänge könnten wir später ergänzen
    }


# === Plaintext ===

def extract_txt(filepath: Path) -> dict:
    """Liest Textdateien direkt ein."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    return {
        "extracted_text": text,
        "metadata": _get_file_metadata(filepath),
        "attachments": [],
    }


# === RTF ===

def extract_rtf(filepath: Path) -> dict:
    """Extrahiert Text aus RTF-Dateien."""
    try:
        from striprtf.striprtf import rtf_to_text
        raw = filepath.read_text(encoding="utf-8", errors="replace")
        text = rtf_to_text(raw)
    except ImportError:
        # Fallback macOS: textutil
        import subprocess
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(filepath)],
                capture_output=True, text=True, timeout=30
            )
            text = result.stdout if result.returncode == 0 else "[RTF konnte nicht gelesen werden]"
        except FileNotFoundError:
            text = "[RTF konnte nicht gelesen werden - striprtf nicht installiert]"

    return {
        "extracted_text": text,
        "metadata": _get_file_metadata(filepath),
        "attachments": [],
    }


# === Hilfsfunktionen ===

def _get_file_metadata(filepath: Path) -> dict:
    """Liest Basis-Metadaten aus dem Dateisystem."""
    stat = filepath.stat()
    return {
        "filename": filepath.name,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(),
    }


def save_extracted_text(result: dict, output_dir: Path) -> Path:
    """Speichert den extrahierten Text als Markdown-Datei."""
    output_dir.mkdir(parents=True, exist_ok=True)

    source = Path(result["source_file"]).stem
    md_path = output_dir / f"{source}.md"

    # Markdown mit Metadaten schreiben
    lines = [
        f"# {result['source_file']}",
        f"",
        f"**Typ:** {result['file_type']}",
    ]

    if "date" in result.get("metadata", {}):
        lines.append(f"**Datum:** {result['metadata']['date']}")
    if "subject" in result.get("metadata", {}):
        lines.append(f"**Betreff:** {result['metadata']['subject']}")

    lines.extend([
        "",
        "---",
        "",
        result["extracted_text"],
    ])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
