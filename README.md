# Newcase LM

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-000000.svg?logo=ollama)](https://ollama.com)
[![Gemma 4](https://img.shields.io/badge/Model-Gemma%204%2031B-4285F4.svg?logo=google&logoColor=white)](https://ai.google.dev/gemma)

**Your on-premise AI briefing pipeline for legal professionals.**

Drop your case files — contracts, court decisions, emails, scanned PDFs, whatever you've got — and Newcase LM reads them, extracts the facts, connects the dots across documents, and delivers two things: a **confidential case briefing** for your team, and a **fully anonymized prompt** ready to send to any cloud AI (Claude, ChatGPT, Gemini) without exposing a single name, address, or case number.

Everything runs locally on your machine. Nothing leaves your network. Ever.

### 🎬 Demo

[![Watch the demo](https://img.youtube.com/vi/ZLX6WC37dHg/maxresdefault.jpg)](https://www.youtube.com/watch?v=ZLX6WC37dHg)

> *Click the image to watch the full walkthrough on YouTube (3 min)*

**Manages multiple cases side by side.** Each case lives in its own folder under `~/Desktop/newcase/cases/`. The pipeline opens with an interactive case picker — pick an existing case to continue work on it, or create a new one in one step. Switching between cases requires no file shuffling.

**Ships with incremental updates.** Add a new document to an existing case, run the pipeline again — only the new file gets processed (cached by SHA-256 hash), and the briefing is regenerated with the additional context. No redundant LLM work.

**Handles multilingual cases out of the box.** With `gemma4:31b-mlx-bf16`, foreign-language documents (e.g. Slovenian, Italian, English) are processed and summarized directly in German — no separate translation pass required. See the "Multilingual support" section below for hardware notes.

## 🎯 Who is this for?

Anyone who works with legal documents and wants AI assistance without compromising confidentiality: law firms, in-house legal teams, courts, government agencies, compliance departments, insurance companies. If you handle sensitive case files and need structured briefings, this is for you.

## ⚙️ How it works

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

## 🔒 Privacy & confidentiality

Newcase LM is built for environments where confidentiality is non-negotiable — whether that's attorney-client privilege, professional secrecy obligations, or internal compliance policies. All processing (text extraction, LLM inference, anonymization) happens locally via [Ollama](https://ollama.com). No API calls to external services. No telemetry. No cloud. Your case data stays on your hardware.

## 📦 Installation

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

## 🚀 Usage

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

## 📄 Output

The `output/` folder will contain:

| File | What it is |
|------|-----------|
| `KLARTEXT_*.docx` | Full case briefing with real names (confidential) |
| `KLARTEXT_*.md` | Same as Markdown |
| `ANON_*.docx` | Anonymized version — safe for cloud AI prompts |
| `ANON_*.md` | Same as Markdown |
| `*_klartext.md` | Individual document summaries |

## 🔄 Incremental updates

The pipeline caches document summaries based on file hashes. When you add a new document to an existing case and re-run the pipeline, only the new document is summarized — cached results are reused. The overall briefing is then regenerated with the full context, including the new material.

To reset the cache, delete the `.cache/` folder inside your input directory.

## 🖥️ Hardware, model & scaling

### Tested configuration

Successfully tested on a **Mac Studio M4 Max (64GB Unified Memory)** running **Gemma4-31B Q8** with a **32k token context window**. Processing time is approximately 5 minutes per document with Thinking Mode enabled. A 4-document case (contracts, emails with attachments, expert opinions) runs end-to-end in about 25 minutes including the anonymization pass.

### Model

The default model is [Gemma 4 31B](https://ai.google.dev/gemma) by Google, running in Q8 quantization (~30GB) with Thinking Mode enabled. This delivers high-quality, coherent legal narratives in German. The model and all prompt templates are configured in `config.py` — swap in any Ollama-compatible model that fits your hardware and language.

### Two-stage prompting (optional)

For email-heavy or unstructured cases, you can switch stage 2 from a single LLM call to a two-pass extract-then-write pipeline. Pass 1 produces a structured fact list (people, dates, amounts, references) without narrative. Pass 2 formulates the narrative from those facts only, with no access to the original. This significantly reduces hallucinations and improves entity attribution — for example, identifying a sender's role correctly when only their email signature ("Heinz", "Heinzi") is in the corpus.

Trade-off: doubled stage-2 runtime (in practice 30–40% overhead, since each pass is shorter), and on already well-structured documents (contracts, decrees) it can omit fine details that the single-stage prompt picks up directly from the original text. For that reason, it's an opt-in via `NEWCASE_TWO_STAGE=true` rather than the default. Email documents (.msg/.eml) keep their dedicated mail prompt regardless of this flag.

### Context window

The default context window is 32,768 tokens (`NUM_CTX` in `config.py`). This comfortably handles individual documents up to ~60,000 characters and combined briefings from up to ~10–15 documents. For larger cases, raise it via environment variables — no code change required:

```bash
# Choose a different model than the default gemma4:31b-it-q8_0
export NEWCASE_OLLAMA_MODEL=qwen3.6:35b-a3b-mlx-bf16

# Larger context (more KV-cache RAM, but Ollama handles it automatically)
export NEWCASE_NUM_CTX=131072            # 128k tokens (Qwen3 with YaRN, etc.)
export NEWCASE_MAX_TEXT_LENGTH=200000    # ~50k tokens per single document

# Longer Ollama timeout for big, slow models (default: 1800 = 30 min).
# A 122B model at ~20 tok/s producing a ~20k-token anonymisation needs
# roughly 18 min — set higher if you see "Ollama Timeout" errors:
export NEWCASE_OLLAMA_TIMEOUT=3600       # 60 min

# Two-stage prompting (default off). When enabled, stage 2 runs as two LLM
# calls per document: pass 1 extracts structured facts, pass 2 formulates
# the narrative from those facts only. Significantly reduces hallucinations
# on email-heavy or unstructured cases (IT, business correspondence, mail
# threads with unclear roles). On clearly structured documents (contracts,
# decrees, court files) it can sometimes lose detail vs. single-stage —
# so keep it as opt-in:
export NEWCASE_TWO_STAGE=true            # enable for messy email-heavy cases
```

Put these in your `~/.zshrc` (or `~/.bashrc`) to make them permanent. Sensible defaults by hardware:

| RAM | `NEWCASE_NUM_CTX` | `NEWCASE_MAX_TEXT_LENGTH` |
|---|---|---|
| 64 GB | 32768 (default) | 60000 (default) |
| 128 GB+ | 65536 – 131072 | 120000 – 200000 |

### Hardware requirements

| Platform | What works |
|----------|-----------|
| **Mac (Apple Silicon)** | 64GB+ Unified Memory — tested and recommended. M3/M4 Max or Ultra. |
| **Windows / Linux (Nvidia)** | RTX 4090 (24GB VRAM) runs Q4. RTX 3090, A6000, or A100 for Q8. |
| **Budget option** | Use `gemma4:31b-it-q4_0` (~18GB) on 32GB machines — works, slightly drier output. |

Ollama handles model loading and GPU offloading automatically. If it runs, it runs.

### Cloud backends (optional)

For benchmarking against frontier models or when local hardware is unavailable, the LLM stages (2, 3a, 3b) can run against any OpenAI-compatible API instead of a local Ollama instance. **Text extraction (stage 1) always stays local** — no document content leaves your machine until the LLM call.

Configuration is purely via environment variables, no code change required:

```bash
# Switch the backend
export NEWCASE_BACKEND=openai_compat
export NEWCASE_API_KEY=<your-api-key>

# Pick a provider:

# Mistral (EU-hosted — preferable for GDPR-sensitive data)
export NEWCASE_API_BASE_URL=https://api.mistral.ai/v1
export NEWCASE_MODEL=mistral-large-latest

# OpenAI
export NEWCASE_API_BASE_URL=https://api.openai.com/v1
export NEWCASE_MODEL=gpt-5

# Anthropic (OpenAI-compatible endpoint)
export NEWCASE_API_BASE_URL=https://api.anthropic.com/v1
export NEWCASE_MODEL=claude-sonnet-4-6

# Back to local Ollama
export NEWCASE_BACKEND=ollama
```

The same `NEWCASE_OLLAMA_TIMEOUT` applies — cloud calls can be slow when contexts get large.

⚠️ **Confidentiality considerations for legal use.** Sending case material through a cloud API means the data leaves your jurisdiction. Most providers contractually exclude API data from training (Mistral, OpenAI, Anthropic all do as of 2026), but the data is still processed and logged in their datacenters. For real client work consider:

- **Run the anonymization stage (3b) locally first**, then feed only the anonymized output to the cloud for further analysis. The `ANON_*.md` file is designed exactly for that use case.
- **Prefer EU-hosted providers** (Mistral, Aleph Alpha, …) over US-hosted ones if GDPR compliance matters to you. EU-Datacenter, no US CLOUD Act exposure.
- **Have a data processing agreement (DPA / AVV)** in place with whoever you use.
- For pure benchmarking with non-real-client data: just use the cloud directly and compare against your local output.

### Multilingual support

The default model `qwen3.6:35b-a3b-mlx-bf16` works well for German cases. For cases containing documents in other languages (Slovenian, Italian, English, French, …), switch to `gemma4:31b-mlx-bf16` — it reads the source language and writes the briefing directly in German, without a separate translation step:

```bash
NEWCASE_OLLAMA_MODEL=gemma4:31b-mlx-bf16 python pipeline.py
```

Tested with Slovenian legal documents on Apple Silicon: 5 documents extracted, summarized and merged into a clean German briefing in roughly 30 minutes. Names, dates, amounts and legal references are preserved exactly as in the source.

⚠️ **Hardware note**: Gemma 4 31B in BF16 is a **dense** model — all 31 billion parameters are read per token, vs. only ~3 B for the MoE default. RAM footprint is similar (~62 GB), but token throughput drops from ~60 tok/s to ~10 tok/s on Apple Silicon. Plan for a Mac with **64 GB+ unified memory** and roughly **5× longer pipeline runs** compared to the default Qwen MoE model. For larger cases (20+ documents), consider whether the time investment is worth it case by case. For pure German cases, the default Qwen model is faster and equally accurate.

### Multi-case workflow

The pipeline manages multiple cases side by side. Each case lives in its own subfolder under `~/Desktop/newcase/cases/` with isolated `input/`, `extracted/`, `output/` and `.cache/` directories. No more shuffling files between runs.

```
~/Desktop/newcase/
└── cases/
    ├── 2026-05-03_satiamo/
    │   ├── vertrag.pdf, email.msg, …   ← PDFs / MSGs / EMLs directly in the case root
    │   ├── extracted/                   ← stage-1 output (markdown)
    │   ├── output/                      ← klartext + anon summaries, chat saves
    │   └── .cache/                      ← per-document hash cache
    └── 2026-04-30_stadler-bau/
        └── ...
```

Documents live directly in the case folder — no extra `input/` subdirectory. The pipeline ignores the `extracted/`, `output/` and `.cache/` subfolders when scanning for input documents (only files directly in the case root are processed).

**Starting the pipeline:**

```bash
python pipeline.py                            # interactive case picker
python pipeline.py --case stadler             # pick by name or substring
python pipeline.py --new-case mietsache       # create new case + use it
python pipeline.py --list-cases               # list and exit
```

Creating a new case interactively suggests `YYYY-MM-DD_neu` as the default. You can override with any free-form name — it gets slugified (umlauts → ae/oe/ue, spaces → `_`) and prefixed with today's date. So typing `Stadler Mietsache` becomes `2026-05-03_stadler_mietsache`.

`chat.py` uses the same selection logic — pick which case to chat about, then it auto-loads the latest `KLARTEXT_*.md` from that case's `output/`.

**Migration from single-case setup:** if you have an older `~/Desktop/newcase/` with `extracted/`, `output/` etc. directly inside (no `cases/` subfolder), the pipeline prints a hint on first run. Create your first case (`--new-case` or interactive `N`), then move your input files (PDFs, MSGs, …) directly into the new case folder and move `extracted/`, `output/`, `.cache/` underneath it.

## 🗂️ Project structure

```
├── pipeline.py       # Main orchestration script
├── case_layer.py     # Multi-case management (discovery, creation, CLI)
├── chat.py           # Interactive REPL against a case's briefing
├── config.py         # Configuration, prompts, model settings
├── extractor.py      # Text extraction (PDF, DOCX, MSG, OCR)
├── summarizer.py     # LLM calls (Ollama), anonymization
├── docx_export.py    # Markdown → Word document conversion
├── requirements.txt  # Python dependencies
└── .gitignore        # Protects output from accidental commits
```

## ⚡ Configuration

All prompts, model settings, and paths are in `config.py`. Key settings:

- `OLLAMA_MODEL` — which model to use (default: `gemma4:31b-it-q8_0`)
- `INPUT_DIR` / `OUTPUT_DIR` — where to read and write
- `ENABLE_VERIFICATION` — optional fact-checking loop (default: off)
- Prompt templates for summarization and anonymization — tune these to your jurisdiction and language

The default prompts are optimized for Austrian legal documents in German. Adjust them for your needs.

## 🔗 Related: Pre-AI Redaction Tool

If you need fine-grained, interactive control over redaction — for instance, reviewing and adjusting which entities get anonymized before sending a document anywhere — check out [Pre-AI Redaction Workflow](https://github.com/HeinzTempl/pre_ai_redaction_workflow_legal_professional_V3). It's a standalone Streamlit app with NER-based entity detection, drag-and-drop document upload, and a learning system that improves over time. Works well as a complement to Newcase LM for cases where you want a human in the loop on the redaction step.

## 📝 License

MIT License — see [LICENSE](LICENSE).

Use it, fork it, build on it. If you make something cool, let me know.
