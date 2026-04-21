# Newcase LM

**Your on-premise AI briefing pipeline for legal professionals.**

Drop your case files — contracts, court decisions, emails, scanned PDFs, whatever you've got — and Newcase LM reads them, extracts the facts, connects the dots across documents, and delivers two things: a **confidential case briefing** for your team, and a **fully anonymized prompt** ready to send to any cloud AI (Claude, GPT-4, Gemini) without exposing a single name, address, or case number.

Everything runs locally on your machine. Nothing leaves your network. Ever.

**Watch the demo:** [Newcase LM on YouTube](https://www.youtube.com/watch?v=ZLX6WC37dHg)

**Ships with incremental updates:** Add a new document to an existing case, run the pipeline again — only the new file gets processed, and the briefing is regenerated with the additional context. No redundant work.

## Who is this for?

Anyone who works with legal documents and wants AI assistance without compromising confidentiality: law firms, in-house legal teams, courts, government agencies, compliance departments, insurance companies. If you handle sensitive case files and need structured briefings, this is for you.

## How it works

```
Your case files (PDF, DOCX, MSG, EML, TXT, RTF, ...)
    │
    ▼
[Stage 1]  Text extraction (OCR, email parsing, attachment unpacking)
    │
    ▼
[Stage 2]  Per-document summaries via local LLM (full detail, real names)
    │
    ▼
[Stage 3a] Case briefing ──────→ KLARTEXT_*.docx + .md
    │                             (confidential, for your team)
    ▼
[Stage 3b] Anonymization ──────→ ANON_*.docx + .md
                                  (cloud-ready prompt, no PII)
```

The pipeline uses real names and details throughout Stages 2 and 3a — this produces dramatically better cross-document person matching and coherent narratives. Anonymization happens as a separate final step on the already-polished briefing, which means cleaner and more consistent redaction.

## Privacy & confidentiality

Newcase LM is built for environments where confidentiality is non-negotiable — whether that's attorney-client privilege, professional secrecy obligations, or internal compliance policies. All processing (text extraction, LLM inference, anonymization) happens locally via [Ollama](https://ollama.com). No API calls to external services. No telemetry. No cloud. Your case data stays on your hardware.

## Installation

### Prerequisites

- **Python 3.10+**
- **Ollama** — local LLM runtime ([ollama.com](https://ollama.com))
- **40GB+ RAM** for the recommended model (see Hardware below)
- **Tesseract** (optional, for scanned/image PDFs)

### Setup

```bash
# 1. Clone this repo
git clone https://github.com/HeinzTempl/newcase-lm.git
cd newcase-lm

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Ollama and pull the model
# Download Ollama from https://ollama.com (macOS, Windows, Linux)
ollama pull gemma4:31b-it-q8_0    # ~30GB download, best quality

# 4. Tesseract for scanned PDFs (optional)
# macOS:
brew install tesseract tesseract-lang
# Windows:
# Download from https://github.com/UB-Mannheim/tesseract/wiki
# Linux:
# sudo apt install tesseract-ocr tesseract-ocr-deu
```

## Usage

```bash
# Drop your case files into the input folder (default: ~/Desktop/newcase/)
# Then run:
python3 pipeline.py

# Extract text only (no LLM summarization)
python3 pipeline.py --extract-only

# Process a single file
python3 pipeline.py --file ~/Desktop/newcase/contract.pdf

# Skip anonymization (briefing only)
python3 pipeline.py --skip-anon

# Custom input/output directories
python3 pipeline.py --input-dir ~/Cases/Smith --output-dir ~/Cases/Smith/output
```

## Output

The `output/` folder will contain:

| File | What it is |
|------|-----------|
| `KLARTEXT_*.docx` | Full case briefing with real names (confidential) |
| `KLARTEXT_*.md` | Same as Markdown |
| `ANON_*.docx` | Anonymized version — safe for cloud AI prompts |
| `ANON_*.md` | Same as Markdown |
| `*_klartext.md` | Individual document summaries |

## Incremental updates

The pipeline caches document summaries based on file hashes. When you add a new document to an existing case and re-run the pipeline, only the new document is summarized — cached results are reused. The overall briefing is then regenerated with the full context, including the new material.

To reset the cache, delete the `.cache/` folder inside your input directory.

## Hardware, model & scaling

### Tested configuration

Successfully tested on a **Mac Studio M4 Max (64GB Unified Memory)** running **Gemma4-31B Q8** with a **32k token context window**. Processing time is approximately 5 minutes per document with Thinking Mode enabled. A 4-document case (contracts, emails with attachments, expert opinions) runs end-to-end in about 25 minutes including the anonymization pass.

### Model

The default model is [Gemma 4 31B](https://ai.google.dev/gemma) by Google, running in Q8 quantization (~30GB) with Thinking Mode enabled. This delivers high-quality, coherent legal narratives in German. The model and all prompt templates are configured in `config.py` — swap in any Ollama-compatible model that fits your hardware and language.

### Context window

The default context window is 32,768 tokens (`num_ctx` in `config.py`). This comfortably handles individual documents up to ~60,000 characters and combined briefings from up to ~10–15 documents. For larger cases, increase `num_ctx` to 49,152 or 65,536 — this costs more RAM for the KV cache but Ollama handles it automatically.

### Hardware requirements

| Platform | What works |
|----------|-----------|
| **Mac (Apple Silicon)** | 64GB+ Unified Memory — tested and recommended. M3/M4 Max or Ultra. |
| **Windows / Linux (Nvidia)** | RTX 4090 (24GB VRAM) runs Q4. RTX 3090, A6000, or A100 for Q8. |
| **Budget option** | Use `gemma4:31b-it-q4_0` (~18GB) on 32GB machines — works, slightly drier output. |

Ollama handles model loading and GPU offloading automatically. If it runs, it runs.

## Project structure

```
├── pipeline.py       # Main orchestration script
├── config.py         # Configuration, prompts, model settings
├── extractor.py      # Text extraction (PDF, DOCX, MSG, OCR)
├── summarizer.py     # LLM calls (Ollama), anonymization
├── docx_export.py    # Markdown → Word document conversion
├── requirements.txt  # Python dependencies
└── .gitignore        # Protects output from accidental commits
```

## Configuration

All prompts, model settings, and paths are in `config.py`. Key settings:

- `OLLAMA_MODEL` — which model to use (default: `gemma4:31b-it-q8_0`)
- `INPUT_DIR` / `OUTPUT_DIR` — where to read and write
- `ENABLE_VERIFICATION` — optional fact-checking loop (default: off)
- Prompt templates for summarization and anonymization — tune these to your jurisdiction and language

The default prompts are optimized for Austrian legal documents in German. Adjust them for your needs.

## Related: Pre-AI Redaction Tool

If you need fine-grained, interactive control over redaction — for instance, reviewing and adjusting which entities get anonymized before sending a document anywhere — check out [Pre-AI Redaction Workflow](https://github.com/HeinzTempl/pre_ai_redaction_workflow_legal_professional_V3). It's a standalone Streamlit app with NER-based entity detection, drag-and-drop document upload, and a learning system that improves over time. Works well as a complement to Newcase LM for cases where you want a human in the loop on the redaction step.

## License

MIT License — see [LICENSE](LICENSE).

Use it, fork it, build on it. If you make something cool, let me know.
