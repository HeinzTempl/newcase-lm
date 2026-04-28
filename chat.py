"""
Kanzlei Pipeline v2 - Interaktiver Akten-Chat
==============================================
Befragung der von pipeline.py erzeugten Akten-Übersicht.

Lädt automatisch das neueste KLARTEXT_*.md aus dem Output-Ordner plus
alle Einzelzusammenfassungen, packt sie als System-Kontext in den
Konversationsverlauf und führt einen REPL-Chat gegen das in config.py
konfigurierte Ollama-Modell.

Aufruf:
    python chat.py                          # neueste Akte aus INPUT_DIR
    python chat.py --briefing PFAD          # explizites Briefing
    python chat.py --case ~/Desktop/falla   # anderes Akten-Verzeichnis
    python chat.py --full                   # Originaltexte direkt mitladen

Slash-Befehle siehe /help im laufenden Chat.
"""

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OUTPUT_DIR,
    EXTRACTED_DIR,
    INPUT_DIR,
    NUM_CTX,
)

logger = logging.getLogger(__name__)


# === ANSI-Color-Codes für CLI ===
class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"


# === System-Prompt für den Akten-Chat ===
CHAT_SYSTEM_PROMPT = """Du bist ein juristischer Sachverhaltsreferent für eine Anwaltskanzlei.
Du hast Zugriff auf die Akte eines Falles: eine Gesamtübersicht (Briefing) und die
Einzelzusammenfassungen aller Dokumente.

REGELN:
1. Antworte AUSSCHLIESSLICH basierend auf dem dir vorliegenden Akteninhalt.
2. Wenn eine Information NICHT in der Akte steht, sag das EXPLIZIT
   (z.B. "Dazu gibt der Akteninhalt nichts her").
3. Erfinde NICHTS, spekuliere NICHT, gib KEINE rechtliche Bewertung
   und KEINE Handlungsempfehlungen.
4. Verwende echte Namen, Daten, Aktenzeichen, Geldbeträge und Adressen
   exakt so, wie sie in der Akte stehen. Geldbeträge immer mit Währung.
5. Bei Widersprüchen zwischen Dokumenten: nenne diese explizit und
   verweise auf die widersprüchlichen Quellen.
6. Antworte auf Deutsch, präzise und sachlich. Lange Antworten gerne in
   strukturierter Form (z.B. mit Aufzählung), kurze Antworten in Fließtext.
7. Wenn der Anwender nach dem Inhalt eines bestimmten Dokuments fragt,
   beziehe dich primär auf die Einzelzusammenfassung dieses Dokuments.
"""


# === Helper-Funktionen ===

def find_latest_briefing(output_dir: Path) -> Optional[Path]:
    """Findet das neueste KLARTEXT_*.md im output_dir."""
    candidates = list(output_dir.glob("KLARTEXT_*.md"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_summaries(output_dir: Path) -> list[tuple[str, str]]:
    """Lädt alle *_klartext.md (außer KLARTEXT_*) als (Name, Inhalt)-Tupel."""
    files = sorted(output_dir.glob("*_klartext.md"))
    files = [f for f in files if not f.name.startswith("KLARTEXT_")]
    return [
        (f.stem.replace("_klartext", ""), f.read_text(encoding="utf-8"))
        for f in files
    ]


def load_extracted(extracted_dir: Path) -> list[tuple[str, str]]:
    """Lädt alle *.md aus extracted_dir (rohe extrahierte Texte)."""
    if not extracted_dir.exists():
        return []
    files = sorted(extracted_dir.glob("*.md"))
    return [(f.stem, f.read_text(encoding="utf-8")) for f in files]


def build_aktencontext(
    briefing: str,
    summaries: list[tuple[str, str]],
    full_mode: bool,
    extracted: list[tuple[str, str]],
) -> str:
    """Baut den vollständigen Akten-Kontext für den System-Prompt."""
    parts = [
        "## Gesamtübersicht (Briefing)",
        briefing,
        "",
        "## Einzelzusammenfassungen",
    ]
    for name, text in summaries:
        parts.append(f"### {name}")
        parts.append(text)
        parts.append("")

    if full_mode and extracted:
        parts.append("## Originaltexte (extrahiert)")
        for name, text in extracted:
            parts.append(f"### {name}")
            parts.append(text)
            parts.append("")

    return "\n".join(parts)


def build_system_message(
    briefing: str,
    summaries: list[tuple[str, str]],
    full_mode: bool,
    extracted: list[tuple[str, str]],
) -> str:
    """Vollständiger System-Prompt: Verhaltensregeln + Akteninhalt."""
    akten = build_aktencontext(briefing, summaries, full_mode, extracted)
    return (
        f"{CHAT_SYSTEM_PROMPT}\n\n"
        f"--- BEGINN AKTENINHALT ---\n"
        f"{akten}\n"
        f"--- ENDE AKTENINHALT ---"
    )


def strip_thinking(content: str) -> str:
    """Entfernt <think>...</think>- bzw. <|think|>-Blöcke aus der Antwort."""
    # Qwen3 Thinking Mode
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    # Gemma4 Thinking-Channel
    content = re.sub(r"<\|channel\|>.*?<\|message\|>", "", content, flags=re.DOTALL)
    content = re.sub(r"<\|think\|>", "", content)
    return content.strip()


def call_ollama_chat(messages: list[dict]) -> tuple[str, dict]:
    """Stellt einen Chat-Call an Ollama. Hält das Modell 30 Min warm."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",  # Modell bleibt zwischen Fragen geladen
        "options": {
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 64,
            "num_ctx": NUM_CTX,
        },
    }

    t_start = time.time()
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=600,
    )
    resp.raise_for_status()
    data = resp.json()
    t_elapsed = time.time() - t_start

    return data["message"]["content"], {
        "prompt_tokens": data.get("prompt_eval_count", 0),
        "output_tokens": data.get("eval_count", 0),
        "elapsed": t_elapsed,
    }


def print_help():
    print(f"""
{C.BOLD}Slash-Befehle:{C.RESET}
  {C.CYAN}/help{C.RESET}            Diese Hilfe anzeigen
  {C.CYAN}/exit{C.RESET}, {C.CYAN}/quit{C.RESET}, {C.CYAN}/q{C.RESET}  Chat beenden
  {C.CYAN}/full{C.RESET}            Originaltexte aus extracted/ zu/abschalten
  {C.CYAN}/reload{C.RESET}          Briefing + Zusammenfassungen neu einlesen
  {C.CYAN}/reset{C.RESET}           Konversationsverlauf zurücksetzen (Akte bleibt)
  {C.CYAN}/tokens{C.RESET}          Token-Stand der letzten Antwort anzeigen
  {C.CYAN}/save [DATEI]{C.RESET}    Konversation als Markdown speichern
""")


def save_conversation(messages: list[dict], briefing_name: str, target: Path):
    """Speichert den Chat (ohne System-Prompt) als Markdown."""
    with open(target, "w", encoding="utf-8") as f:
        f.write("# Akten-Chat\n\n")
        f.write(f"*Briefing: {briefing_name}*  \n")
        f.write(f"*Modell: {OLLAMA_MODEL}*  \n")
        f.write(f"*Erstellt: {datetime.now().strftime('%d.%m.%Y %H:%M')}*\n\n")
        f.write("---\n\n")
        for m in messages[1:]:  # System-Prompt überspringen
            if m["role"] == "user":
                f.write(f"## Frage\n\n{m['content']}\n\n")
            elif m["role"] == "assistant":
                f.write(f"## Antwort\n\n{m['content']}\n\n")


# === Main REPL ===

def main():
    parser = argparse.ArgumentParser(
        description="Interaktiver Akten-Chat gegen lokales Ollama-Modell",
    )
    parser.add_argument(
        "--briefing",
        type=Path,
        help="Pfad zum Briefing (default: neueste KLARTEXT_*.md im Output-Ordner)",
    )
    parser.add_argument(
        "--case",
        type=Path,
        help="Akten-Wurzel (überschreibt INPUT_DIR aus config.py)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Originaltexte aus extracted/ direkt mitladen",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Verzeichnisse bestimmen
    if args.case:
        case_root = args.case.expanduser()
        output_dir = case_root / "output"
        extracted_dir = case_root / "extracted"
    else:
        output_dir = OUTPUT_DIR
        extracted_dir = EXTRACTED_DIR

    # Briefing finden/laden
    briefing_path = args.briefing or find_latest_briefing(output_dir)
    if not briefing_path or not briefing_path.exists():
        print(f"{C.RED}❌ Kein Briefing in {output_dir} gefunden.{C.RESET}")
        print(f"   Erst {C.BOLD}python pipeline.py{C.RESET} laufen lassen, "
              f"dann nochmal hier.")
        sys.exit(1)

    briefing = briefing_path.read_text(encoding="utf-8")
    summaries = load_summaries(output_dir)
    extracted = load_extracted(extracted_dir)
    full_mode = args.full

    # Header
    print(f"\n{C.BOLD}🏛️  Akten-Chat{C.RESET}")
    print(f"{C.DIM}{'─' * 60}{C.RESET}")
    print(f"{C.CYAN}Briefing:{C.RESET}          {briefing_path.name}")
    print(f"{C.CYAN}Modell:{C.RESET}            {OLLAMA_MODEL}")
    print(f"{C.CYAN}Kontext:{C.RESET}           {NUM_CTX:,} Tokens")
    print(f"{C.CYAN}Zusammenfassungen:{C.RESET} {len(summaries)} Dokumente")
    if extracted:
        state = "AKTIV (/full)" if full_mode else "inaktiv (/full zum Zuschalten)"
        print(f"{C.CYAN}Originaltexte:{C.RESET}     {len(extracted)} verfügbar – {state}")
    print(f"{C.DIM}{'─' * 60}{C.RESET}")
    print(f"{C.DIM}Tipp: /help für Befehle, /exit zum Beenden.{C.RESET}\n")

    # Konversation initialisieren
    system_msg = build_system_message(briefing, summaries, full_mode, extracted)
    messages: list[dict] = [{"role": "system", "content": system_msg}]
    last_stats: Optional[dict] = None

    while True:
        try:
            user_input = input(f"{C.GREEN}Frage:{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.DIM}Auf Wiedersehen.{C.RESET}")
            break

        if not user_input:
            continue

        # === Slash-Befehle ===
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit", "/q"):
                print(f"{C.DIM}Auf Wiedersehen.{C.RESET}")
                break

            elif cmd == "/help":
                print_help()

            elif cmd == "/full":
                full_mode = not full_mode
                messages[0]["content"] = build_system_message(
                    briefing, summaries, full_mode, extracted
                )
                state = "AKTIVIERT" if full_mode else "DEAKTIVIERT"
                print(f"{C.YELLOW}Originaltexte {state}.{C.RESET}\n")

            elif cmd == "/reload":
                briefing_path_now = args.briefing or find_latest_briefing(output_dir)
                if briefing_path_now:
                    briefing = briefing_path_now.read_text(encoding="utf-8")
                    summaries = load_summaries(output_dir)
                    extracted = load_extracted(extracted_dir)
                    messages[0]["content"] = build_system_message(
                        briefing, summaries, full_mode, extracted
                    )
                    print(f"{C.YELLOW}Akte neu geladen "
                          f"({briefing_path_now.name}).{C.RESET}\n")
                else:
                    print(f"{C.RED}Kein Briefing gefunden zum Reload.{C.RESET}\n")

            elif cmd == "/reset":
                messages = [messages[0]]
                last_stats = None
                print(f"{C.YELLOW}Konversation zurückgesetzt.{C.RESET}\n")

            elif cmd == "/tokens":
                if last_stats:
                    total = last_stats["prompt_tokens"] + last_stats["output_tokens"]
                    pct = total / NUM_CTX * 100
                    print(f"{C.CYAN}Letzte Antwort:{C.RESET} "
                          f"{last_stats['prompt_tokens']:,} prompt + "
                          f"{last_stats['output_tokens']:,} output = "
                          f"{total:,} Tokens ({pct:.1f}% von {NUM_CTX:,})\n")
                else:
                    print(f"{C.DIM}Noch keine Antwort gegeben.{C.RESET}\n")

            elif cmd == "/save":
                if arg:
                    save_path = Path(arg).expanduser()
                else:
                    fn = f"chat_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
                    save_path = output_dir / fn
                try:
                    save_conversation(messages, briefing_path.name, save_path)
                    print(f"{C.YELLOW}Gespeichert:{C.RESET} {save_path}\n")
                except OSError as e:
                    print(f"{C.RED}Fehler beim Speichern: {e}{C.RESET}\n")

            else:
                print(f"{C.RED}Unbekannter Befehl '{cmd}'. /help für Übersicht.{C.RESET}\n")
            continue

        # === Normale Frage ===
        messages.append({"role": "user", "content": user_input})

        try:
            print(f"{C.DIM}🤔 Modell denkt nach...{C.RESET}", end="\r", flush=True)
            answer_raw, stats = call_ollama_chat(messages)
            print(" " * 60, end="\r")  # Spinner-Zeile löschen
        except requests.exceptions.RequestException as e:
            print(f"{C.RED}Fehler bei Ollama-Anfrage: {e}{C.RESET}\n")
            messages.pop()  # Frage zurücknehmen, da keine Antwort
            continue

        last_stats = stats
        answer = strip_thinking(answer_raw)
        messages.append({"role": "assistant", "content": answer})

        # Antwort ausgeben
        print(f"{C.BLUE}Antwort:{C.RESET}\n{answer}\n")

        # Token-Statistik
        total = stats["prompt_tokens"] + stats["output_tokens"]
        pct = total / NUM_CTX * 100
        warn = f"  {C.RED}⚠️ NEAR LIMIT{C.RESET}" if pct > 90 else ""
        tps = stats["output_tokens"] / stats["elapsed"] if stats["elapsed"] > 0 else 0
        ctx_label = f"{NUM_CTX // 1024}k" if NUM_CTX % 1024 == 0 else f"{NUM_CTX}"
        print(
            f"{C.DIM}  📊 {total:,} tokens "
            f"({pct:.1f}% von {ctx_label}) | "
            f"{tps:.1f} tok/s | "
            f"{stats['elapsed']:.0f}s{C.RESET}{warn}\n"
        )


if __name__ == "__main__":
    main()
