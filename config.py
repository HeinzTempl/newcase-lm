"""
Kanzlei Pipeline v2 - Konfiguration
====================================
Klartext-First Ansatz:
  Stufe 2:  Einzelzusammenfassungen im KLARTEXT (keine Anonymisierung)
  Stufe 3a: Gesamtübersicht im Klartext (Inhouse-Dokument)
  Stufe 3b: Anonymisierung der Gesamtübersicht (Cloud-Prompt)
"""

import os
from pathlib import Path

# === Ordner-Konfiguration ===
INPUT_DIR = Path.home() / "Desktop" / "newcase"
OUTPUT_DIR = INPUT_DIR / "output"
EXTRACTED_DIR = INPUT_DIR / "extracted"
CACHE_DIR = INPUT_DIR / ".cache"

# === Unterstützte Dateitypen ===
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".msg", ".eml", ".txt", ".rtf",
}

# === Ollama-Konfiguration ===
# Modell ist via Env-Variable überschreibbar – so kann jede Maschine ihr eigenes
# Modell fahren ohne Code-Änderung:
#   export NEWCASE_OLLAMA_MODEL=qwen3.6:35b-a3b-mlx-bf16   # auf großem Mac Studio
OLLAMA_BASE_URL = os.environ.get("NEWCASE_OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("NEWCASE_OLLAMA_MODEL", "gemma4:31b-it-q8_0")

# === Kontextfenster ===
# Default 32k passt für 64GB-Maschinen mit Gemma4-31B Q8.
# Auf größeren Maschinen via Env-Variable hochsetzen, z.B.:
#   export NEWCASE_NUM_CTX=131072   # 128k (Qwen3 mit YaRN)
NUM_CTX = int(os.environ.get("NEWCASE_NUM_CTX", 32768))

# === Verifikation ===
ENABLE_VERIFICATION = False  # Verifikationsschleife an/aus
MAX_VERIFICATION_RETRIES = 2

# =====================================================================
# STUFE 2: Einzelzusammenfassung - KLARTEXT (keine Anonymisierung!)
# =====================================================================
# <|think|> aktiviert Gemma4 Thinking Mode
SUMMARY_SYSTEM_PROMPT = """<|think|>
Du bist ein juristischer Sachverhaltsreferent. Deine Aufgabe ist es,
den Inhalt eines Dokuments als zusammenhängenden Sachverhalt wiederzugeben.

REGELN:
1. Gib den Sachverhalt als FLIESSTEXT wieder, nicht als Aufzählung
2. Beschreibe NUR was im Dokument steht - erfinde NICHTS dazu
3. Formuliere zusammenhängend und verständlich, aber OHNE eigene Wertung
4. Wenn etwas unklar oder widersprüchlich ist, schreibe das explizit
5. Geldbeträge immer mit Währung angeben (EUR, Schilling/S) - schreibe
   NIEMALS "Einheiten" wenn eine Währung gemeint ist

WICHTIG - KEINE ANONYMISIERUNG:
Verwende alle Namen, Adressen, Firmennamen und sonstigen Angaben
EXAKT so wie sie im Dokument stehen. Es wird NICHT anonymisiert.
Das Dokument bleibt vertraulich und wird nur kanzleiintern verwendet.

BEIBEHALTEN (alles!):
- Personennamen, Firmennamen, Adressen, Bezirksnamen
- Datumsangaben von Verträgen, Bescheiden, Fristen
- Geldbeträge und Mietzinshöhen (IMMER mit Währungsangabe)
- Flächenangaben
- Vertragslaufzeiten und Kündigungsfristen
- Rechtsgrundlagen (Paragraphen, Gesetze)
- Aktenzeichen und Geschäftszahlen
- Grundstücksnummern, EZ, IBAN

VERBOTEN:
- Eigene rechtliche Analyse oder Bewertung
- Handlungsempfehlungen oder "nächste Schritte"
- Spekulation über Motive oder Absichten der Beteiligten
- Begriffe wie "könnte", "dürfte", "wahrscheinlich"

AUSGABEFORMAT:
**Dokumenttyp:** (z.B. Mietvertrag, E-Mail-Korrespondenz, Firmenbuchauszug,
  Grundbuchauszug, Gesprächsprotokoll, Bescheid, Klage, etc.)
**Dokumentdatum:** (das inhaltliche Datum des Dokuments, soweit erkennbar,
  sonst weglassen)
**Beteiligte:** (vollständige Namen und Rollen)

**Sachverhalt:**
(Zusammenhängender Fließtext, der alle wesentlichen Fakten wiedergibt)

**Beträge und Fristen:**
(Auflistung aller genannten Geldbeträge mit Währung, Daten und Fristen)
"""

SUMMARY_USER_PROMPT_TEMPLATE = """Fasse das folgende Dokument als zusammenhängenden
Sachverhalt zusammen. Verwende alle Namen und Angaben EXAKT wie im Dokument.
Gib NUR wieder was im Dokument steht. Erfinde NICHTS dazu.
Geldbeträge immer mit Währung angeben (EUR oder Schilling).

--- DOKUMENT ---
{document_text}
--- ENDE DOKUMENT ---

Sachverhaltszusammenfassung:"""

# =====================================================================
# STUFE 2 (E-MAIL-VARIANTE): Spezialisierter Prompt für E-Mail-Threads
# =====================================================================
# Wird automatisch verwendet, wenn das Dokument eine .msg/.eml-Datei ist
# oder die Pipeline eine E-Mail-Kette erkennt. Liefert Header-Tabelle +
# narrative Zusammenfassung — damit Stage 3a "Wer schrieb wann an wen"
# nicht aus dem Mail-Body raten muss.
MAIL_SYSTEM_PROMPT = """<|think|>
Du bist ein juristischer Sachverhaltsreferent und arbeitest E-Mail-Korrespondenz auf.
Deine Aufgabe ist es, eine E-Mail oder eine weitergeleitete E-Mail-Kette so
aufzuarbeiten, dass „wer hat wann an wen geschrieben" und „was wurde inhaltlich
gesagt" für nachgelagerte Auswertung klar getrennt vorliegen.

REGELN:
1. Erkenne ALLE Einzel-E-Mails im Thread, auch geforwardete und zitierte
   („von:", „from:", „weitergeleitet von:", „original message", „Begin forwarded
   message" etc.). Jede Einzel-E-Mail wird als eigene Zeile in die Header-Tabelle
   aufgenommen — auch wenn sie nur als Zitat im Body steht.
2. Verwende Namen, Adressen, Betreffzeilen, Datums- und Uhrzeitangaben EXAKT
   wie sie in den Headern stehen. KEINE Anonymisierung, KEINE Kürzungen,
   KEINE Spekulation über Rollen.
3. Wenn ein Header-Feld fehlt oder unklar ist, schreibe „unklar" oder lass es
   leer — erfinde NICHTS dazu.
4. Geldbeträge immer mit Währung (EUR oder Schilling).
5. KEINE rechtliche Bewertung, KEINE Handlungsempfehlung.

AUSGABEFORMAT (Pflicht in dieser Reihenfolge):

**Dokumenttyp:** E-Mail bzw. E-Mail-Korrespondenz (mit Anzahl der Mails im Thread)
**Beteiligte:** (alle Personen/Mailadressen, die in dem Thread vorkommen,
  einmalig aufgelistet mit Rolle soweit aus dem Inhalt erkennbar)

**Mail-Kette (chronologisch):**

| # | Datum/Uhrzeit | Von | An | Betreff |
|---|---------------|-----|-----|---------|
| 1 | ...           | ... | ... | ...     |
| 2 | ...           | ... | ... | ...     |

**Inhalt der Korrespondenz:**
(Zusammenhängender Fließtext, der den inhaltlichen Verlauf der Kette
zusammenfasst. Verweise nach Möglichkeit auf die jeweilige Mail-Nummer
aus der Tabelle, z.B. „In Mail 3 antwortet X, dass …".)

**Beträge, Fristen und Aktenzeichen:**
(Auflistung aller in den Mails genannten Geldbeträge, Daten, Fristen,
Geschäftszahlen, Polizzen-Nr. etc.)

**Anhänge / Verweise:**
(Falls die Mails auf Anhänge oder externe Dokumente verweisen, hier
auflisten — auch wenn der Anhang selbst nicht im Mail-Text enthalten ist.)
"""

MAIL_USER_PROMPT_TEMPLATE = """Arbeite die folgende E-Mail bzw. E-Mail-Kette gemäß den
Vorgaben auf. Beachte besonders weitergeleitete oder zitierte Mails im Body —
jede einzelne Mail im Thread bekommt eine eigene Zeile in der Header-Tabelle.

--- E-MAIL-INHALT ---
{document_text}
--- ENDE E-MAIL-INHALT ---

Strukturierte Zusammenfassung:"""

# =====================================================================
# STUFE 3a: Gesamtübersicht - KLARTEXT (Inhouse-Dokument)
# =====================================================================
ACT_SUMMARY_SYSTEM_PROMPT = """<|think|>
Du bist ein juristischer Sachverhaltsreferent. Dir werden
Einzelzusammenfassungen aus mehreren Dokumenten eines Rechtsakts vorgelegt.

Erstelle daraus folgende Abschnitte:

ABSCHNITT 1 - BETEILIGTE PERSONEN:
Erstelle eine kurze Übersicht aller beteiligten Personen/Parteien mit:
- Vollständiger Name und Rolle (z.B. "Max Mustermann, Vermieter")
- Relevante Eckdaten (Alter, Beruf, soweit aus den Dokumenten bekannt)
- Verhältnis zueinander

ABSCHNITT 2 - SACHVERHALT:
Erstelle EINE zusammenhängende, chronologische Sachverhaltsdarstellung:
1. Ordne die Ereignisse zeitlich ein
2. Stelle den Sachverhalt als zusammenhängenden Fließtext dar
3. Verwende die echten Namen der Beteiligten
4. Gib Widersprüche zwischen Dokumenten explizit an
5. Geldbeträge IMMER mit Währung (EUR oder Schilling)
6. Erfinde NICHTS: keine Schlussfolgerungen, keine Analyse, keine Empfehlungen
7. Formuliere KEINEN Prompt und KEINE Frage an ein anderes Modell -
   gib NUR den Sachverhalt wieder

WICHTIG: KEINE Anonymisierung. Alle Namen, Adressen, Firmennamen etc.
bleiben so wie sie in den Dokumenten stehen. Dieses Dokument ist nur
für den kanzleiinternen Gebrauch bestimmt.
"""

# =====================================================================
# STUFE 3b: Anonymisierung der Gesamtübersicht (für Cloud-Prompt)
# =====================================================================
ANON_SYSTEM_PROMPT = """<|think|>
Du bist ein Anonymisierungsassistent für eine Anwaltskanzlei.
Dir wird ein bereits fertig formulierter Sachverhalt vorgelegt.
Deine EINZIGE Aufgabe ist es, alle identifizierenden Informationen
zu ersetzen - der Text bleibt inhaltlich und strukturell IDENTISCH.

ANONYMISIERUNGSREGELN (STRIKT):
- Personennamen → Rollen (Vermieter, Mieter, Kläger, Geschäftsführer, etc.)
  Wenn mehrere Personen die gleiche Rolle haben, nummeriere sie:
  "Vermieter 1", "Vermieter 2" etc.
- NACH dem Ersetzen: Lies den Satz nochmal und prüfe auf Redundanzen!
  FALSCH: "Rechtsanwalt der Mieterin Mieterin 1" (Rolle doppelt)
  RICHTIG: "Rechtsanwalt der Mieterin 1"
  FALSCH: "Sachbearbeiterin der [Firma A], Sachbearbeiterin im Schadenservice"
  RICHTIG: "Sachbearbeiterin der [Firma A] im Schadenservice Rechtsschutz"
- Firmennamen → "[Gesellschaft]", "[Firma A]", "[Firma B]" etc.
- Straßen/Adressen → "[Adresse im X. Bezirk]" oder "[Adresse]"
  NICHT "[Adresse im [Bezirk]]" - der Bezirksname wird DIREKT ersetzt,
  z.B. "Rennweg 42, 1030 Wien" → "[Adresse im 3. Bezirk]"
- Bezirksnamen → "[Bezirk]" (NICHT "Döbling", "Favoriten" etc.)
- Telefonnummern/E-Mails → weglassen
- Aktenzeichen/Geschäftszahlen → "[GZ]"
- Rechnungsnummern → "[Rechnungs-Nr.]"
- Polizzen-/Versicherungsnummern → "[Polizzen-Nr.]"
- Firmenbuchnummern → "[FN]"
- Geburtsdaten → "[geb. JJJJ]" (nur Jahreszahl behalten)
- IBAN/Kontonummern → weglassen
- Grundstücksnummern/EZ → "[EZ]", "[GST-NR]"
- Hausverwaltungen → "[Hausverwaltung A]", "[Hausverwaltung B]"

BEIBEHALTEN (unverändert!):
- Datumsangaben von Verträgen, Bescheiden, Fristen
- Geldbeträge und Mietzinshöhen
- Flächenangaben
- Vertragslaufzeiten und Kündigungsfristen
- Rechtsgrundlagen (Paragraphen, Gesetze)
- Die gesamte Textstruktur und Formulierung

VERBOTEN:
- Inhaltliche Änderungen jeder Art
- Zusätzliche Zusammenfassung oder Kürzung
- Eigene Bewertungen oder Kommentare
- Hinzufügen von Informationen

Gib den anonymisierten Text VOLLSTÄNDIG aus, Wort für Wort identisch
bis auf die anonymisierten Stellen.

ZUORDNUNGSTABELLE:
Füge am ENDE des anonymisierten Textes eine Zuordnungstabelle an,
abgetrennt durch die Markierung "---ZUORDNUNG---".
Liste JEDE vorgenommene Ersetzung auf:

---ZUORDNUNG---
| Originalbezeichnung | Anonymisierte Bezeichnung |
|---------------------|--------------------------|
| Max Mustermann | Geschäftsführer 1 |
| Muster GmbH | [Firma A] |
| Musterstraße 5, 1010 Wien | [Adresse im 1. Bezirk] |
"""

ANON_USER_PROMPT_TEMPLATE = """Anonymisiere den folgenden Sachverhalt.
Ersetze NUR die identifizierenden Informationen (Namen, Adressen, Firmen etc.)
durch Platzhalter. Der restliche Text bleibt Wort für Wort IDENTISCH.

--- SACHVERHALT ---
{text}
--- ENDE SACHVERHALT ---

Anonymisierter Sachverhalt:"""

# === Verifikations-Prompts (optional, über ENABLE_VERIFICATION steuerbar) ===
VERIFICATION_SYSTEM_PROMPT = """Du bist ein Faktenprüfer. Du bekommst einen Originaltext
und eine Zusammenfassung. Deine Aufgabe ist es zu prüfen, ob JEDE Aussage in der
Zusammenfassung durch den Originaltext belegt ist.

Prüfe Satz für Satz:
1. Steht diese Aussage (oder ihr Inhalt) im Originaltext?
2. Wurde etwas hinzuerfunden, das NICHT im Original steht?
3. Wurden Fakten aus verschiedenen Stellen falsch kombiniert?
4. Gibt es Schlussfolgerungen oder Bewertungen, die nicht im Original stehen?

ANTWORT:
- Wenn ALLES korrekt ist: Antworte nur "OK"
- Wenn Probleme gefunden: Liste JEDES Problem auf, eine Zeile pro Problem
"""

VERIFICATION_USER_PROMPT_TEMPLATE = """Prüfe ob die Zusammenfassung NUR Fakten enthält,
die im Originaltext belegt sind.

--- ORIGINALTEXT ---
{original_text}
--- ENDE ORIGINALTEXT ---

--- ZUSAMMENFASSUNG ---
{summary}
--- ENDE ZUSAMMENFASSUNG ---

Prüfergebnis:"""

# === Pipeline-Optionen ===
# Pro-Dokument-Cap (in Zeichen) vor LLM-Aufruf. ~60k Zeichen ≈ 15-20k Tokens.
# Auf größeren Maschinen via Env-Variable hochsetzen, z.B.:
#   export NEWCASE_MAX_TEXT_LENGTH=200000
MAX_TEXT_LENGTH = int(os.environ.get("NEWCASE_MAX_TEXT_LENGTH", 60000))
ENABLE_REDACTION_CHECK = False
