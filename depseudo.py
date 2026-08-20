#!/usr/bin/env python3
"""
Kanzlei Pipeline v2 - Rückweg der Pseudonymisierung
====================================================
Setzt in einem Entwurf (z. B. aus einem Cloud-LLM zurückgekommen) die
Platzhalter [PERSON_1], [FIRMA_2], ... wieder durch die echten Namen ein.

Gegenstück zur deterministischen Anonymisierung (pseudonymizer.py). Die
Zuordnungstabelle wird über den Fall (--case) oder direkt (--map-id)
gefunden; das Ergebnis landet in einer DATEI, nicht auf stdout — damit die
Klarnamen nicht versehentlich durch ein Terminal-Log oder einen Chat laufen.

Verwendung:
    python depseudo.py --case pixner --file entwurf.md
    python depseudo.py --case pixner --file entwurf.md --out klage_final.md
    python depseudo.py --map-id 14Cg88-25k --file entwurf.md
    python depseudo.py --list             # vorhandene Zuordnungstabellen
"""

import argparse
import sys
from pathlib import Path

import case_layer
import pseudonymizer
from config import PSEUDONYM_DIR


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Platzhalter in einem Entwurf wieder durch Klarnamen ersetzen"
    )
    parser.add_argument("--case", help="Fallname (die map_id ist der Fallname)")
    parser.add_argument("--map-id", help="Zuordnungstabelle direkt angeben "
                                         "(statt --case)")
    parser.add_argument("--file", type=Path, help="Entwurf mit Platzhaltern")
    parser.add_argument("--out", type=Path,
                        help="Zieldatei (Default: <entwurf>_depseudo.md daneben)")
    parser.add_argument("--list", action="store_true",
                        help="Vorhandene Zuordnungstabellen anzeigen (ohne Klarnamen)")
    args = parser.parse_args()

    if args.list:
        rows = pseudonymizer.list_maps(PSEUDONYM_DIR)
        if not rows:
            print(f"Keine Zuordnungstabellen in {PSEUDONYM_DIR}.")
            return 0
        print(f"Zuordnungstabellen in {PSEUDONYM_DIR}:")
        for r in rows:
            cats = ", ".join(f"{k}:{v}" for k, v in sorted(r["categories"].items())) or "-"
            print(f"  {r['map_id']:24s} {r['entries']:3d} Einträge, "
                  f"{r['aliases']} Kurzformen  ({cats})  zuletzt {r['modified']}")
        return 0

    if not args.file:
        parser.error("--file fehlt (der Entwurf mit den Platzhaltern)")
    if not args.file.exists():
        print(f"Datei nicht gefunden: {args.file}", file=sys.stderr)
        return 1

    # map_id bestimmen: direkt oder über den Fall
    if args.map_id:
        map_id = args.map_id
    elif args.case:
        case = None
        for c in case_layer.discover_cases():
            if c.name == args.case or args.case.lower() in c.name.lower():
                case = c
                break
        map_id = case.name if case else args.case
    else:
        parser.error("--case oder --map-id fehlt")

    src = args.file.read_text(encoding="utf-8")
    try:
        out, n, unresolved = pseudonymizer.depseudonymize_text(
            src, map_id, PSEUDONYM_DIR
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        rows = pseudonymizer.list_maps(PSEUDONYM_DIR)
        if rows:
            print("Vorhanden: " + ", ".join(r["map_id"] for r in rows),
                  file=sys.stderr)
        return 1

    out_path = args.out or args.file.with_name(f"{args.file.stem}_depseudo.md")
    out_path.write_text(out, encoding="utf-8")

    print(f"{n} Platzhalter ersetzt. Ergebnis: {out_path}")
    if unresolved:
        print("Nicht aufgelöst (nicht in der Tabelle): " + ", ".join(unresolved))
        print("→ Prüfen, ob der Entwurf Platzhalter aus einer anderen map_id "
              "verwendet oder ob das Cloud-Modell Platzhalter verändert hat.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
