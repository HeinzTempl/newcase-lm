# Kanzlei-Pipeline: Setup-Anleitung

## 1. Python-Abhängigkeiten installieren

Terminal öffnen und im Pipeline-Ordner ausführen:

```bash
cd ~/Desktop/kanzlei_pipeline
pip3 install -r requirements.txt
```

**Hinweis:** Docling ist das größte Paket (~1-2 GB) wegen der ML-Modelle für Layouterkennung.
Falls es Probleme macht, funktioniert die Pipeline auch ohne (Fallback auf PyMuPDF/python-docx).

## 2. Ollama installieren

### Download
Lade Ollama von https://ollama.com herunter und installiere die Mac-App.

### Modell laden
Nach der Installation, im Terminal:

```bash
# Option A: SauerkrautLM (sehr gut auf Deutsch, braucht ~20GB RAM)
ollama pull sauerkrautlm-qwen-32b

# Option B: Qwen3-30B-A3B (schneller, weniger RAM, MoE-Modell)
ollama pull qwen3:30b-a3b
```

### Testen ob es läuft

```bash
ollama list    # Zeigt installierte Modelle
ollama run sauerkrautlm-qwen-32b "Sage Hallo"   # Kurztest
```

**Wenn du ein anderes Modell verwendest**, ändere `OLLAMA_MODEL` in `config.py`.

## 3. Pipeline verwenden

### Alle Dokumente im newcase-Ordner verarbeiten
```bash
cd ~/Desktop/kanzlei_pipeline
python3 pipeline.py
```

### Nur Text extrahieren (ohne LLM)
```bash
python3 pipeline.py --extract-only
```

### Einzelne Datei verarbeiten
```bash
python3 pipeline.py --file ~/Desktop/newcase/vertrag.pdf
```

## 4. Ordnerstruktur

```
~/Desktop/newcase/           ← Hier Dokumente reinlegen
    vertrag.pdf
    kuendigung.docx
    email_korrespondenz.msg
    output/                  ← Zusammenfassungen (automatisch erstellt)
        vertrag_zusammenfassung.md
        kuendigung_zusammenfassung.md
        email_korrespondenz_zusammenfassung.md
        GESAMT_20260416_1430.md    ← Chronologische Gesamtübersicht
    extracted/               ← Extrahierter Rohtext (automatisch erstellt)
        vertrag.md
        kuendigung.md
        email_korrespondenz.md
```

## 5. Workflow

1. Bild-PDFs vorher durch ABBY FineReader → Text-PDFs
2. Alle Dokumente in `~/Desktop/newcase/` legen
3. `python3 pipeline.py` ausführen
4. Einzelzusammenfassungen reviewen in `newcase/output/`
5. `GESAMT_*.md` als Prompt in Cloud-Modell verwenden
