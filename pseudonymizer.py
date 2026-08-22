"""
Kanzlei Pipeline v2 - Deterministische Pseudonymisierung
=========================================================
Ersetzt Klarnamen und andere Identifikatoren durch stabile Platzhalter
([PERSON_1], [FIRMA_2], ...) und kann sie später wieder zurückersetzen.

Kernidee: Die Ersetzung passiert deterministisch in Python, NICHT durch
ein generatives Modell. Was nicht in der Zuordnungstabelle steht, wird
nicht angefasst — Beträge, Daten und Fristen bleiben garantiert unverändert.
Ein LLM darf optional *Kandidaten* vorschlagen (siehe EXTRACT_PROMPT und
parse_extraction_json), aber die eigentliche Ersetzung bleibt Textersetzung.

Die Zuordnungstabelle liegt als JSON pro map_id (typisch: Fall-/Aktenname)
in einem Maps-Verzeichnis — getrennt vom Output, damit die Datei, die nach
außen geht, niemals die Klarnamen trägt.

Map-Format (kompatibel zu bestehenden pseudonym_maps/*.json):
    {
      "map":   {"[PERSON_1]": "Ing. Franz Hofstetter", ...},
      "alias": {"Hofstetter": "[PERSON_1]", ...}
    }

Dieses Modul ist bewusst ohne Abhängigkeiten (nur Standardbibliothek) und
ohne Projekt-Imports gehalten, damit es auch außerhalb der Pipeline
verwendet werden kann (z. B. von einem MCP-Server importiert).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# === Kategorien und Platzhalter ===

CAT_TAGS = {
    "person": "PERSON", "firma": "FIRMA", "adresse": "ADRESSE",
    "az": "AZ", "iban": "IBAN", "kfz": "KFZ", "sonst": "SONST",
}

PLACEHOLDER_RX = re.compile(r"\[[A-Z]+_\d+\]")

# === Prompt für die LLM-Kandidatensuche ===
# Das Modul ruft selbst KEIN Modell auf. Wer Kandidaten per LLM suchen will,
# schickt diesen Prompt als System-Prompt an ein (lokales!) Modell und gibt
# die Antwort durch parse_extraction_json().
EXTRACT_PROMPT = (
    "Du bist ein Extraktionswerkzeug. Lies den Text und gib ALLE personenbezogenen "
    "Bezeichner aus, die eine Person oder ein Unternehmen identifizierbar machen: "
    "Namen natuerlicher Personen, Firmennamen, Anschriften, Aktenzeichen/Geschaeftszahlen, "
    "IBAN/Kontonummern, KFZ-Kennzeichen, E-Mail-Adressen, Telefonnummern.\n"
    "Gib AUSSCHLIESSLICH ein JSON-Array zurueck, ohne Erklaerung, ohne Codefence:\n"
    '[{"wert": "woertlich im Text vorkommende Zeichenkette", "kategorie": '
    '"person|firma|adresse|az|iban|kfz|sonst"}]\n'
    "Regeln: 'wert' muss exakt und woertlich im Text stehen. Keine Duplikate. "
    "Keine Gattungsbegriffe (Klaeger, Beklagte, Gericht, Zeuge). Keine Datumsangaben. "
    "Zeichenketten der Form [PERSON_1], [FIRMA_2], [ADRESSE_3] sind bereits gesetzte "
    "Platzhalter -- diese NIEMALS ausgeben. Wenn nichts zu finden ist: []"
)

# === Deterministische Muster — greifen unabhängig vom Modell ===
SWEEP_PATTERNS = [
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")),
    ("sonst", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),                      # E-Mail
    ("az", re.compile(r"\b\d{1,3}\s?[A-Za-z]{1,4}\s?\d{1,5}/\d{2}[a-z]\b")),      # GZ österr.
    ("kfz", re.compile(r"\b[A-Z]{1,2}-\d{1,5}[A-Z]{1,3}\b")),
    # Telefon: Lookbehind verhindert, dass Datumsteile in Dateinamen
    # ('CamScanner 21-06-2026 15.24.pdf') als Nummer gelesen werden.
    ("sonst", re.compile(r"(?<![\d-])(?:\+43|\+\d{1,3}|0)[\d /-]{7,}\d")),        # Telefon
]

# Bestandteile, die als Kurzform (Alias) nichts taugen — bewusst auch
# Gattungsbegriffe, die in Firmennamen stecken, aber generisch im Text
# vorkommen (sonst wird 'die Hausverwaltung' zum Platzhalter).
_ALIAS_STOP = {
    "gmbh", "ag", "kg", "og", "gesmbh", "gesellschaft", "mbh", "co", "cokg",
    "dr", "mag", "ing", "dipl", "prof", "herr", "frau", "der", "die", "das",
    "und", "von", "van", "gasse", "strasse", "straße", "platz", "wien", "graz",
    "linz", "salzburg", "innsbruck", "klagenfurt",
    "bank", "holding", "gruppe", "partner", "immobilien", "verwaltung",
    "hausverwaltung", "gebäudeverwaltung", "gebaeudeverwaltung", "bauträger",
    "bautraeger", "versicherung", "kanzlei", "rechtsanwalt", "rechtsanwälte",
}

# Titel/Anreden vor einem Nachnamen ('Dr. Abramovici', 'Frau Fischbach')
_TITLES_RX = re.compile(
    r"^(?:(?:DDr|Dr|MMag|Mag|Ing|DI|Dipl\.?-?Ing|Prof|Univ\.?-?Prof)\.?\s+"
    r"|(?:Magister|Magistra|Herr|Herrn|Frau)\s+)+"
)

# Straße + Hausnummer innerhalb einer Anschrift ('Siebeneichengasse 1-3',
# 'Schönbrunner Straße 218') — wird als Kurzform der Anschrift mitersetzt.
_STREET_RX = re.compile(
    r"(?:[A-ZÄÖÜ][\wäöüß.\-]*(?:gasse|straße|strasse|weg|platz|allee|ring|zeile|steig|kai|lände|markt)"
    r"|[A-ZÄÖÜ][\wäöüß.\-]*\s+(?:Gasse|Straße|Strasse|Weg|Platz|Allee|Ring|Zeile|Steig|Kai))"
    r"\s+\d+(?:\s*[-–/]\s*\d+)?[a-z]?"
)


@dataclass
class PseudoResult:
    """Ergebnis eines Pseudonymisierungslaufs."""
    text: str                       # der pseudonymisierte Text
    map_path: str                   # Pfad der Zuordnungstabelle (JSON)
    total_entries: int              # Einträge in der Tabelle insgesamt
    new_entries: int                # davon in diesem Lauf neu vergeben
    hits: dict = field(default_factory=dict)      # Platzhalter -> Ersetzungszahl
    alias_hits: int = 0             # Ersetzungen über Kurzformen (Nachname etc.)
    residuals: list = field(default_factory=list) # Platzhalter, deren Klarwert NOCH im Text steht

    @property
    def clean(self) -> bool:
        """True = kein bekannter Klarwert steht mehr im Ergebnis."""
        return not self.residuals


# === Hilfsfunktionen ===

def _flex(val: str) -> re.Pattern:
    """Findet den Wert auch, wenn im Text ein Zeilenumbruch statt eines
    Leerzeichens steht ('Schönbrunner\\nStraße 218')."""
    parts = [re.escape(p) for p in val.split()]
    return re.compile(r"\s+".join(parts)) if len(parts) > 1 else re.compile(re.escape(val))


def _collapse(text: str) -> str:
    """'[FIRMA_1] & [FIRMA_1] GmbH' -> '[FIRMA_1] GmbH'."""
    return re.sub(r"(\[[A-Z]+_\d+\])(?:\s*(?:&|und|-|,)?\s*\1)+", r"\1", text)


def _aliases(value: str, cat: str) -> list[str]:
    """Kurzformen eines Personen-/Firmennamens.

    Bei Personen nur der Nachname — Vornamen sind zu häufig, ein zweiter
    'Johann' im selben Akt bekäme sonst fälschlich denselben Platzhalter.
    Bei Anschriften die Straße samt Hausnummer — sonst bleibt
    'Siebeneichengasse 1-3' stehen, wenn nur die Variante mit Türnummer
    in der Tabelle steht."""
    if cat == "adresse":
        m = _STREET_RX.search(value)
        return [m.group(0)] if m and m.group(0) != value else []
    if cat not in ("person", "firma"):
        return []
    toks = []
    for tok in re.split(r"[\s,]+", value):
        tok = tok.strip(".,;:()[]")
        if len(tok) < 4 or not tok[0].isupper() or not tok.isalpha():
            continue
        if tok.lower() in _ALIAS_STOP:
            continue
        toks.append(tok)
    if not toks:
        return []
    out = [toks[-1]] if cat == "person" else toks
    return [t for t in out if t != value]


def parse_terms(terms: str) -> list[tuple[str, str]]:
    """Zerlegt eine terms-Angabe in (wert, kategorie)-Paare.

    Getrennt wird an Zeilenumbruch oder Semikolon — bewusst NICHT am Komma,
    weil Anschriften fast immer eines enthalten. Format je Eintrag:
    'Begriff' oder 'Begriff=kategorie' (person|firma|adresse|az|iban|kfz|sonst).
    """
    found = []
    for raw in [x.strip() for x in re.split(r"[\n;]+", terms or "") if x.strip()]:
        if "=" in raw:
            val, _, cat = raw.partition("=")
            found.append((val.strip(), cat.strip().lower()))
        else:
            found.append((raw, "sonst"))
    return found


def parse_extraction_json(raw: str) -> list[tuple[str, str]] | None:
    """Parst die LLM-Antwort auf EXTRACT_PROMPT (JSON-Array) in (wert, kategorie).

    Gibt None zurück, wenn kein verwertbares JSON gefunden wurde — dann sollte
    der Aufrufer den Lauf abbrechen oder mit vorgegebenen terms wiederholen.
    """
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(obj, list):
        return None
    found = []
    for it in obj:
        if isinstance(it, dict) and str(it.get("wert", "")).strip():
            found.append((str(it["wert"]).strip(),
                          str(it.get("kategorie", "sonst")).strip().lower()))
    return found


def regex_sweep(src: str) -> list[tuple[str, str]]:
    """Deterministische Mustersuche: IBAN, E-Mail, GZ, Kennzeichen, Telefon."""
    swept = []
    for cat, rx in SWEEP_PATTERNS:
        for m in rx.findall(src):
            v = m.strip() if isinstance(m, str) else ""
            if len(v) > 5:
                swept.append((v, cat))
    # Teiltreffer verwerfen (die Telefonmaske erwischt sonst Teile einer IBAN)
    return [(v, cat) for v, cat in swept
            if not any(v != o and v in o for o, _c in swept)]


# === Map-Verwaltung ===

def map_path(map_id: str, maps_dir: str | Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", map_id)
    return os.path.join(str(maps_dir), safe + ".json")


def load_map(path: str | Path) -> tuple[dict, dict]:
    """Gibt (mapping, alias) zurück. Akzeptiert auch das alte flache Format."""
    if not os.path.isfile(path):
        return {}, {}
    try:
        obj = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}, {}
    if isinstance(obj, dict) and "map" in obj:
        return obj.get("map", {}), obj.get("alias", {})
    return (obj, {}) if isinstance(obj, dict) else ({}, {})


def save_map(path: str | Path, mapping: dict, alias: dict) -> None:
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    json.dump({"map": mapping, "alias": alias},
              open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def list_maps(maps_dir: str | Path) -> list[dict]:
    """Vorhandene Zuordnungstabellen — ohne Klarnamen, nur Statistik."""
    maps_dir = str(maps_dir)
    rows = []
    if not os.path.isdir(maps_dir):
        return rows
    for fn in sorted(os.listdir(maps_dir)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(maps_dir, fn)
        mapping, alias = load_map(fp)
        cats: dict = {}
        for ph in mapping:
            m = re.match(r"\[([A-Z]+)_", ph)
            if m:
                cats[m.group(1)] = cats.get(m.group(1), 0) + 1
        rows.append({
            "map_id": fn[:-5],
            "entries": len(mapping),
            "aliases": len(alias),
            "categories": cats,
            "modified": time.strftime("%d.%m.%Y %H:%M",
                                      time.localtime(os.path.getmtime(fp))),
        })
    return rows


# === Kern: deterministische Ersetzung ===

def _ci_get(d: dict, key: str):
    """Case-insensitiver Lookup ('FRIEDA' findet die Kurzform 'Frieda')."""
    if key in d:
        return d[key]
    kl = key.lower()
    for k, v in d.items():
        if k.lower() == kl:
            return v
    return None


def _find_residuals(text: str, mapping: dict, alias: dict) -> list:
    """Bekannte Klarwerte UND deren Kurzformen, die noch im Text stehen.

    Die Kurzform-Prüfung fängt auch Fälle, in denen ein Wert nur als
    Teilstück auftaucht ('Siebeneichengasse 1-3' ohne Türnummer, blanker
    Nachname) — Gattungsbegriffe aus _ALIAS_STOP werden nicht geprüft,
    damit generische Wörter keinen Fehlalarm auslösen."""
    rev = {v: k for k, v in mapping.items()}
    hits = {rev[v] for v in mapping.values() if v and _flex(v).search(text)}
    for al, ph in alias.items():
        if len(al) < 4 or al.lower() in _ALIAS_STOP:
            continue
        if re.search(r"(?<![\w\[])" + re.escape(al) + r"(?![\w\]])", text):
            hits.add(ph)
    return sorted(hits)


def apply(src: str, mapping: dict, alias: dict,
          found: list[tuple[str, str]],
          use_aliases: bool = True) -> PseudoResult:
    """Reine Ersetzung ohne Datei-I/O: erweitert mapping/alias um die
    gefundenen Werte und ersetzt alles im Text. mapping/alias werden
    IN PLACE fortgeschrieben (bestehende Platzhalter bleiben stabil)."""
    rev = {v: k for k, v in mapping.items()}
    counters: dict = {}
    for ph in mapping:
        m = re.match(r"\[([A-Z]+)_(\d+)\]", ph)
        if m:
            counters[m.group(1)] = max(counters.get(m.group(1), 0), int(m.group(2)))

    # Vollnamen zuerst — dann wird 'Hofstetter' als Kurzform von
    # 'Ing. Franz Hofstetter' erkannt und nicht als zweite Person geführt
    found = sorted(found, key=lambda t: len(t[0]), reverse=True)

    new_count = 0
    for val, cat in found:
        if not val or val in rev:
            continue
        if PLACEHOLDER_RX.search(val):
            continue  # bereits gesetzter Platzhalter — schützt vor Doppelläufen
        if val in alias:  # bereits als Kurzform einer bekannten Person/Firma vergeben
            rev[val] = alias[val]
            continue

        # Varianten derselben Person/Firma/Adresse zusammenführen, statt einen
        # zweiten Platzhalter zu vergeben ('Dr. Abramovici' neben
        # 'Dr. Ileana Abramovici' wäre sonst PERSON_4 neben PERSON_1).
        merged_ph = None
        if use_aliases and cat == "person":
            # Titel + blanker Nachname -> Platzhalter des vollen Namens.
            # 'Andrea Hofstetter' bleibt dagegen eine eigene Person, auch wenn
            # es schon einen 'Ing. Franz Hofstetter' gibt.
            stripped = _TITLES_RX.sub("", val).strip()
            if stripped != val and " " not in stripped:
                merged_ph = _ci_get(alias, stripped)
        elif use_aliases and cat == "firma":
            # nur wenn ALLE brauchbaren Namensbestandteile auf denselben
            # Platzhalter zeigen ('Rustler' -> volle Hausverwaltung; eine
            # 'Rustler Immobilien GmbH' bekäme weiter ihren eigenen).
            # Case-insensitiv, damit 'FRIEDA RUSTLER ... KG' (Großschreibung
            # im Rechnungskopf) nicht als zweite Firma geführt wird.
            toks = _aliases(val, "firma")
            phs = {_ci_get(alias, t) for t in toks} if toks else set()
            if toks and None not in phs and len(phs) == 1:
                merged_ph = phs.pop()
        elif cat not in ("person", "firma"):
            # Teilstück eines bereits erfassten Werts ('Siebeneichengasse 1-3,
            # Top 4' steckt in '1150 Wien, Siebeneichengasse 1-3, Top 4';
            # E-Mail ohne TLD steckt in der vollen Adresse).
            container = next((v for v in sorted(rev, key=len, reverse=True)
                              if val != v and val in v), None)
            if container:
                merged_ph = rev[container]
        if merged_ph is not None:
            rev[val] = merged_ph
            alias.setdefault(val, merged_ph)  # persistieren für Folgeläufe
            continue

        tag = CAT_TAGS.get(cat, "SONST")
        counters[tag] = counters.get(tag, 0) + 1
        ph = f"[{tag}_{counters[tag]}]"
        mapping[ph] = val
        rev[val] = ph
        new_count += 1
        if use_aliases:
            for al in _aliases(val, cat):
                alias.setdefault(al, ph)

    # längste Werte zuerst ersetzen (sonst zerlegt 'Maier' den 'Maier GmbH'-Treffer)
    out = src
    hits: dict = {}
    for val in sorted(rev, key=len, reverse=True):
        if not val:
            continue
        out, n = _flex(val).subn(rev[val], out)
        if n:
            hits[rev[val]] = hits.get(rev[val], 0) + n

    # Kurzformen erst danach, nur auf Wortgrenze
    alias_hits = 0
    for al in sorted(alias, key=len, reverse=True):
        if al in rev:  # ist selbst ein vollständiger Wert
            continue
        rx = re.compile(r"(?<![\w\[])" + re.escape(al) + r"(?![\w\]])")
        out, n = rx.subn(alias[al], out)
        if n:
            hits[alias[al]] = hits.get(alias[al], 0) + n
            alias_hits += n

    out = _collapse(out)

    # Selbstkontrolle: steht ein bekannter Klarwert (oder eine Kurzform
    # davon) noch im Ergebnis?
    residuals = _find_residuals(out, mapping, alias)

    return PseudoResult(
        text=out, map_path="", total_entries=len(mapping), new_entries=new_count,
        hits=hits, alias_hits=alias_hits, residuals=residuals,
    )


def pseudonymize_text(src: str, map_id: str, maps_dir: str | Path,
                      terms: str = "",
                      found: list[tuple[str, str]] | None = None,
                      use_aliases: bool = True,
                      sweep: bool = True,
                      reset: bool = False) -> PseudoResult:
    """Komfort-Einstieg: Map laden, terms + Muster + vorgefundene Kandidaten
    zusammenführen, ersetzen, Map speichern.

    Args:
        src: zu pseudonymisierender Text.
        map_id: Kennung der Zuordnungstabelle (typisch der Fall-/Aktenname).
                Existiert sie schon, werden neue Werte ergänzt und alte
                Platzhalter stabil weiterverwendet.
        maps_dir: Verzeichnis der Zuordnungstabellen (getrennt vom Output!).
        terms: manuell vorgegebene Begriffe, siehe parse_terms().
        found: bereits ermittelte (wert, kategorie)-Kandidaten, z. B. aus
               einem LLM-Lauf mit EXTRACT_PROMPT + parse_extraction_json().
        use_aliases: Nachnamen/Firmenkurzformen mitersetzen.
        sweep: deterministische Muster (IBAN, E-Mail, GZ, ...) mitlaufen lassen.
        reset: vorhandene Tabelle verwerfen. Nur solange zu dieser map_id
               kein Entwurf unterwegs ist — sonst passt der Rückweg nicht mehr.
    """
    if not map_id.strip():
        raise ValueError("map_id fehlt (z. B. der Fall-/Aktenname).")
    path = map_path(map_id, maps_dir)
    mapping, alias = ({}, {}) if reset else load_map(path)

    all_found = list(parse_terms(terms))
    if sweep:
        all_found.extend(regex_sweep(src))
    if found:
        all_found.extend(found)

    result = apply(src, mapping, alias, all_found, use_aliases=use_aliases)
    save_map(path, mapping, alias)
    result.map_path = path
    return result


def depseudonymize_text(src: str, map_id: str, maps_dir: str | Path
                        ) -> tuple[str, int, list[str]]:
    """Setzt Platzhalter wieder durch die echten Werte ein.

    Returns:
        (text, anzahl_ersetzungen, nicht_aufgelöste_platzhalter)
    Raises:
        FileNotFoundError, wenn es zur map_id keine Tabelle gibt.
    """
    path = map_path(map_id, maps_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Keine Zuordnungstabelle '{map_id}' in {maps_dir}.")
    mapping, _alias = load_map(path)

    out, n = src, 0
    for ph, val in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
        if ph in out:
            n += out.count(ph)
            out = out.replace(ph, val)
    unresolved = sorted(set(PLACEHOLDER_RX.findall(out)))
    return out, n, unresolved


def merge_llm_mapping_table(table_md: str, map_id: str, maps_dir: str | Path
                            ) -> tuple[int, int]:
    """Übernimmt eine vom LLM erzeugte Zuordnungstabelle (Markdown,
    '| Original | Platzhalter |') in die JSON-Map — damit auch im LLM-Modus
    ein Rückweg existiert.

    Es werden NUR Zeilen übernommen, deren Platzhalter die eckige Form
    [XXX_n] bzw. [XXX] hat; freie Rollenbezeichnungen ('Vermieter 1') sind
    für eine automatische Rückersetzung zu riskant und werden übersprungen.

    Returns: (übernommen, übersprungen)
    """
    path = map_path(map_id, maps_dir)
    mapping, alias = load_map(path)
    added = skipped = 0
    for line in (table_md or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        orig, ph = cells[0], cells[1]
        if not orig or not ph or set(orig) <= {"-", " "} or set(ph) <= {"-", " "}:
            continue  # Trennzeile
        if orig.lower().startswith("original"):
            continue  # Kopfzeile
        # Alles in eckigen Klammern gilt als sicherer Platzhalter (auch
        # '[Adresse im 1. Bezirk]'); freie Rollennamen ohne Klammern nicht.
        if re.fullmatch(r"\[[^\[\]\n]{2,80}\]", ph) and re.search(r"[A-Za-zÄÖÜäöü]", ph):
            if ph not in mapping:
                mapping[ph] = orig
                added += 1
        else:
            skipped += 1
    if added:
        save_map(path, mapping, alias)
    return added, skipped


def verify_no_cleartext(text: str, map_id: str, maps_dir: str | Path) -> list[str]:
    """Prüft, ob bekannte Klarwerte (oder Kurzformen) noch im Text stehen.

    Returns: Liste der betroffenen Platzhalter (leer = sauber).
    """
    path = map_path(map_id, maps_dir)
    mapping, alias = load_map(path)
    return _find_residuals(text, mapping, alias)
