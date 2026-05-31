"""
Kanzlei Pipeline v2 - Case Layer
=================================
Multi-Case-Verwaltung. Pro Akt ein eigener Unterordner in cases/.

Verzeichnisstruktur:
    ~/Desktop/newcase/
    └── cases/
        ├── 2026-05-03_satiamo/
        │   ├── *.pdf, *.msg, *.eml   (Eingangsdokumente — direkt im Case-Root)
        │   ├── extracted/             (Stage-1-Output)
        │   ├── output/                (Stage-2/3a/3b-Output, Chat-Saves)
        │   └── .cache/
        ├── 2026-04-30_stadler-bau/
        └── ...

Die Eingangsdokumente liegen direkt im Case-Ordner (kein extra input/-Unterordner),
damit der Drag-and-Drop-Workflow natürlich bleibt. Die Unterordner extracted/,
output/ und .cache/ werden automatisch ignoriert, weil discover_files() nur
einzelne Files (.is_file()) auflistet.

Konsumenten (pipeline.py, chat.py) rufen am Programmstart
`select_or_create_case_from_args(args)` auf und bekommen ein Case-Objekt,
das alle relevanten Pfade kapselt.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# === Verzeichnis-Wurzel ===
# Bleibt absichtlich getrennt von config.py — die Pfade in config.py werden
# nach Case-Auswahl überschrieben (siehe set_config_paths).
ROOT_DIR = Path.home() / "Desktop" / "newcase"
CASES_DIR = ROOT_DIR / "cases"


# === ANSI-Color-Codes für CLI-Output ===
class _C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"


# === Case-Datenstruktur ===

@dataclass
class Case:
    """Ein einzelner Akt mit allen abgeleiteten Pfaden."""
    name: str
    path: Path

    # Kanonische Unterordner
    @property
    def input_dir(self) -> Path:
        # Eingangsdokumente liegen direkt im Case-Root — kein extra Unterordner
        return self.path

    @property
    def output_dir(self) -> Path:
        return self.path / "output"

    @property
    def extracted_dir(self) -> Path:
        return self.path / "extracted"

    @property
    def cache_dir(self) -> Path:
        return self.path / ".cache"

    # Unterordner, die NICHT als Eingangsdateien gezählt werden sollen
    _SUBDIRS = {"output", "extracted", ".cache"}

    def ensure_dirs(self) -> None:
        """Legt Case-Root + Output-Subfolder an, falls noch nicht vorhanden."""
        self.path.mkdir(parents=True, exist_ok=True)
        for d in (self.output_dir, self.extracted_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    def file_count(self) -> int:
        """Zählt Eingangsdateien (alles im Case-Root, ohne Hidden-Files und Unterordner)."""
        if not self.path.exists():
            return 0
        return sum(
            1 for f in self.path.iterdir()
            if f.is_file() and not f.name.startswith(".")
        )

    def last_activity(self) -> Optional[datetime]:
        """Neuester Modify-Zeitpunkt aus Case-Root, output/, extracted/."""
        timestamps: list[float] = []
        for d in (self.path, self.output_dir, self.extracted_dir):
            if d.exists():
                timestamps.append(d.stat().st_mtime)
                # Plus deren Inhalte
                for child in d.iterdir():
                    timestamps.append(child.stat().st_mtime)
        if not timestamps:
            return None
        return datetime.fromtimestamp(max(timestamps))

    @classmethod
    def from_path(cls, path: Path) -> "Case":
        return cls(name=path.name, path=path)


# === Discovery & Create ===

def discover_cases() -> list[Case]:
    """
    Findet alle vorhandenen Akten unter cases/, sortiert nach letzter Aktivität
    (neueste zuerst). Akten ohne Aktivität (frisch angelegt, nie gelaufen)
    erscheinen am Ende der Liste.
    """
    if not CASES_DIR.exists():
        return []
    cases: list[Case] = []
    for d in CASES_DIR.iterdir():
        if d.is_dir() and not d.name.startswith("."):
            cases.append(Case.from_path(d))
    # Sortierschlüssel: last_activity() absteigend, None ans Ende
    def _sort_key(c: Case) -> tuple[int, float]:
        ts = c.last_activity()
        if ts is None:
            return (1, 0.0)  # ohne Aktivität: ans Ende
        return (0, -ts.timestamp())  # mit Aktivität: nach Datum absteigend
    cases.sort(key=_sort_key)
    return cases


_UMLAUT_MAP = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
}


def slugify(name: str) -> str:
    """
    Wandelt einen freien Namen in einen filesystem-tauglichen Slug:
    'Stadler Bau-Sache' → 'stadler_bau-sache'
    'Mietsache Müller' → 'mietsache_mueller'
    """
    s = name.strip()
    for src, dst in _UMLAUT_MAP.items():
        s = s.replace(src, dst)
    s = s.lower()
    # Erlaube Buchstaben, Ziffern, _, -, und alles sonst → _
    s = re.sub(r"[^a-z0-9_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "neu"


def _has_date_prefix(name: str) -> bool:
    """Prüft, ob ein Name schon mit YYYY-MM-DD anfängt."""
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", name))


def _resolve_unique_path(target_name: str) -> Path:
    """Hängt _2, _3, ... an, falls Name schon existiert."""
    path = CASES_DIR / target_name
    counter = 2
    while path.exists():
        path = CASES_DIR / f"{target_name}_{counter}"
        counter += 1
    return path


def create_case(name: Optional[str] = None) -> Case:
    """
    Legt eine neue Akte an.

    - `name=None`: Default `YYYY-MM-DD_neu`
    - `name` ohne Datums-Präfix: `YYYY-MM-DD_<slug>`
    - `name` mit Datums-Präfix (z.B. user tippt `2026-05-04_mein-akt`): unverändert übernehmen
    """
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    if not name:
        target_name = f"{today}_neu"
    elif _has_date_prefix(name):
        # User hat schon ein Datum davor → respektieren
        target_name = name
    else:
        target_name = f"{today}_{slugify(name)}"

    path = _resolve_unique_path(target_name)
    case = Case.from_path(path)
    case.ensure_dirs()
    return case


# === CLI-Argumente ===

def add_case_args(parser: argparse.ArgumentParser) -> None:
    """Hängt die Case-Argumente an einen bestehenden ArgumentParser."""
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        metavar="NAME",
        help="Akte auswählen per Name oder Teil-Name (überspringt interaktive Auswahl)",
    )
    parser.add_argument(
        "--new-case",
        type=str,
        default=None,
        metavar="NAME",
        help="Neue Akte mit gegebenem Namen anlegen und auswählen",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="Verfügbare Akten auflisten und beenden",
    )


def _find_case_by_name_or_substring(needle: str) -> Optional[Case]:
    """
    Sucht eine Akte per Name oder Substring (case-insensitive).
    Wenn mehrere passen: None und Fehlermeldung.
    """
    cases = discover_cases()
    needle_lc = needle.lower()
    # Exakter Match hat Priorität
    exact = [c for c in cases if c.name.lower() == needle_lc]
    if exact:
        return exact[0]
    matches = [c for c in cases if needle_lc in c.name.lower()]
    if not matches:
        print(f"{_C.RED}❌ Keine Akte gefunden zu '{needle}'.{_C.RESET}", file=sys.stderr)
        return None
    if len(matches) > 1:
        print(f"{_C.RED}❌ Mehrere Akten passen zu '{needle}':{_C.RESET}", file=sys.stderr)
        for c in matches:
            print(f"   - {c.name}", file=sys.stderr)
        print(f"\n{_C.DIM}Bitte präziseren Namen angeben.{_C.RESET}", file=sys.stderr)
        return None
    return matches[0]


def _print_case_list(cases: list[Case]) -> None:
    """Listet Akten in einer kompakten Tabelle aus."""
    if not cases:
        print(f"{_C.DIM}Noch keine Akten angelegt.{_C.RESET}")
        return
    for case in cases:
        n = case.file_count()
        last = case.last_activity()
        last_str = last.strftime("%d.%m.%Y") if last else "—"
        print(f"  {case.name}")
        print(f"    {_C.DIM}{n} Dokument(e), zuletzt geändert {last_str}{_C.RESET}")


def select_case_interactive() -> Case:
    """
    Interaktive Akten-Auswahl. Zeigt vorhandene Akten und erlaubt Neuanlage.
    """
    cases = discover_cases()

    print()
    print(f"{_C.BOLD}🏛️  Kanzlei-Pipeline — Akten-Auswahl{_C.RESET}")
    print(f"{_C.DIM}{'─' * 60}{_C.RESET}")

    if not cases:
        print()
        print(f"{_C.YELLOW}Noch keine Akten angelegt — wir legen die erste an.{_C.RESET}")
        return _prompt_new_case()

    print()
    print(f"{_C.BOLD}Vorhandene Akten{_C.RESET} {_C.DIM}(neueste zuoberst){_C.RESET}")
    print()
    for i, case in enumerate(cases, 1):
        n = case.file_count()
        last = case.last_activity()
        last_str = last.strftime("%d.%m.%Y") if last else "—"
        # Markiere den Default (zuletzt verwendete = erste in der Liste)
        marker = f"{_C.YELLOW}★{_C.RESET}" if i == 1 else " "
        print(f"  {marker} {_C.CYAN}{i:2d}){_C.RESET} {case.name}")
        print(f"        {_C.DIM}{n} Dokument(e), zuletzt geändert {last_str}{_C.RESET}")
    print()
    print(f"     {_C.CYAN}N){_C.RESET} Neue Akte anlegen")
    print(f"     {_C.CYAN}Q){_C.RESET} Abbrechen")
    print()
    print(f"{_C.DIM}Tipp: Enter = zuletzt verwendete Akte ({cases[0].name}) erneut bearbeiten.{_C.RESET}")
    print(f"{_C.DIM}      Neue Dokumente im Akten-Ordner werden inkrementell ergänzt;{_C.RESET}")
    print(f"{_C.DIM}      bekannte Dokumente kommen aus dem Cache (kein erneuter LLM-Call).{_C.RESET}")
    print()

    while True:
        try:
            choice = input(f"{_C.GREEN}Auswahl [{_C.YELLOW}1{_C.GREEN}]:{_C.RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        # Leere Eingabe → Default (jüngste Akte)
        if choice == "":
            return cases[0]
        if choice in ("q", "quit", "exit"):
            print(f"{_C.DIM}Abgebrochen.{_C.RESET}")
            sys.exit(0)
        if choice == "n":
            return _prompt_new_case()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(cases):
                return cases[idx]
        except ValueError:
            pass
        print(f"{_C.RED}Bitte Enter (Default), 1–{len(cases)}, N (neu) oder Q (Abbruch) eingeben.{_C.RESET}")


def _prompt_new_case() -> Case:
    """Dialog für die Neuanlage einer Akte."""
    default_name = datetime.now().strftime("%Y-%m-%d") + "_neu"
    print()
    print(f"{_C.BOLD}Neue Akte anlegen{_C.RESET}")
    print(f"{_C.DIM}Vorschlag: [{default_name}]{_C.RESET}")
    try:
        name = input(f"{_C.GREEN}Aktenname (Enter für Vorschlag):{_C.RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    case = create_case(name if name else None)

    print()
    print(f"{_C.YELLOW}  ✓ Neue Akte angelegt:{_C.RESET} {case.path}")
    print(f"{_C.DIM}     Lege die Eingangsdokumente direkt in diesen Ordner.{_C.RESET}")
    print()
    return case


def select_or_create_case_from_args(args: argparse.Namespace) -> Case:
    """
    Hauptentry für CLI-Tools: wertet --list-cases / --new-case / --case aus
    oder fällt auf interaktive Auswahl zurück.
    """
    if getattr(args, "list_cases", False):
        cases = discover_cases()
        _print_case_list(cases)
        sys.exit(0)

    if getattr(args, "new_case", None):
        case = create_case(args.new_case)
        print(f"{_C.YELLOW}  ✓ Neue Akte angelegt:{_C.RESET} {case.path}")
        return case

    if getattr(args, "case", None):
        case = _find_case_by_name_or_substring(args.case)
        if not case:
            sys.exit(1)
        case.ensure_dirs()
        return case

    # Kein CLI-Argument → interaktiv
    return select_case_interactive()


# === Config-Injection ===

def apply_case_to_config(case: Case) -> None:
    """
    Setzt die Pfade in config.py auf den ausgewählten Case.

    Wichtig: muss VOR allen anderen Imports aufgerufen werden, die
    config.INPUT_DIR etc. konsumieren — sonst ziehen die den alten
    Default-Snapshot.

    In der Praxis hat pipeline.py die Imports schon oben, weshalb wir hier
    sowohl das Modul-Attribut als auch die bereits importierten Snapshots
    aktualisieren. Konsumenten, die `from config import INPUT_DIR` nutzen,
    müssen entweder vor dem Patch importieren UND danach erneut lesen, oder
    `import config; config.INPUT_DIR` verwenden.
    """
    import config
    config.INPUT_DIR = case.input_dir
    config.OUTPUT_DIR = case.output_dir
    config.EXTRACTED_DIR = case.extracted_dir
    config.CACHE_DIR = case.cache_dir


# === Migrations-Hinweis ===

def detect_legacy_data() -> bool:
    """
    Erkennt den alten Single-Case-Modus: cases/ existiert nicht (oder leer),
    aber unter ROOT_DIR liegen alte input/output/extracted-Sachen herum.
    """
    if CASES_DIR.exists() and any(CASES_DIR.iterdir()):
        return False
    if not ROOT_DIR.exists():
        return False
    legacy_indicators = [
        ROOT_DIR / "extracted",
        ROOT_DIR / "output",
        ROOT_DIR / ".cache",
    ]
    return any(p.exists() for p in legacy_indicators)


def print_migration_hint() -> None:
    """Hinweis für User, die noch im alten Single-Case-Modus sind."""
    print(f"\n{_C.YELLOW}⚠  Hinweis — Multi-Case-Layer ist neu:{_C.RESET}")
    print(f"{_C.DIM}   In {ROOT_DIR} liegen Daten aus dem alten Single-Case-Modus.{_C.RESET}")
    print(f"{_C.DIM}   Migration in 3 Schritten:{_C.RESET}")
    print(f"{_C.DIM}     1. Lege eine neue Akte an (Option 'N' bzw. --new-case){_C.RESET}")
    print(f"{_C.DIM}     2. Verschiebe die Eingangsdateien (PDFs/MSGs) + extracted/, output/, .cache{_C.RESET}")
    print(f"{_C.DIM}        in den neuen Akten-Ordner (Dateien liegen direkt im Case-Root){_C.RESET}")
    print(f"{_C.DIM}     3. Künftige Akten landen automatisch in cases/{_C.RESET}\n")
