#!/usr/bin/env python3
"""
Diagnose für das Cloud-Backend (Mistral / OpenAI / Anthropic / LM Studio ...).

    python check_cloud.py

Prüft der Reihe nach:
  1. Env-Vars (Backend, Base-URL, Key, Modell)
  2. GET  {base}/models   — Key gültig? Modellname gültig?
  3. POST {base}/chat/completions mit einem Mini-Prompt — und zeigt bei
     einem Fehler den KOMPLETTEN Response-Body, nicht nur "400 Bad Request".
"""

import json
import os
import sys

import requests

from config import (
    NEWCASE_BACKEND,
    NEWCASE_API_BASE_URL,
    NEWCASE_API_KEY,
    OLLAMA_MODEL,
)


def base_url() -> str:
    base = (NEWCASE_API_BASE_URL or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/chat/completion", "/completions"):
        if base.endswith(suffix):
            print(f"  ! Base-URL enthielt den Endpoint — gekürzt auf {base[:-len(suffix)]}")
            base = base[: -len(suffix)]
    return base


def main() -> int:
    print("=== 1. Konfiguration ===")
    print(f"  NEWCASE_BACKEND      : {NEWCASE_BACKEND}")
    print(f"  NEWCASE_API_BASE_URL : {NEWCASE_API_BASE_URL or '(nicht gesetzt)'}")
    key = NEWCASE_API_KEY or ""
    print(f"  NEWCASE_API_KEY      : {(key[:6] + '…' + key[-4:]) if key else '(nicht gesetzt)'}")
    print(f"  NEWCASE_MODEL        : {OLLAMA_MODEL}")

    if NEWCASE_BACKEND != "openai_compat":
        print("\n  ! NEWCASE_BACKEND ist nicht 'openai_compat' — die Pipeline würde Ollama nutzen.")
    if not key or not NEWCASE_API_BASE_URL:
        print("\n  ✗ Key oder Base-URL fehlt — abgebrochen.")
        return 1

    base = base_url()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    print(f"\n=== 2. GET {base}/models ===")
    ids = []
    try:
        r = requests.get(f"{base}/models", headers=headers, timeout=20)
        print(f"  HTTP {r.status_code}")
        if r.ok:
            ids = sorted({m.get("id") for m in r.json().get("data", []) if m.get("id")})
            print(f"  {len(ids)} Modelle verfügbar")
            if OLLAMA_MODEL in ids:
                print(f"  ✓ '{OLLAMA_MODEL}' ist gültig")
            else:
                print(f"  ✗ '{OLLAMA_MODEL}' ist KEIN gültiges Modell dieser API")
                print("    Verfügbar u.a.: " + ", ".join(ids[:15]))
        else:
            print(f"  Body: {r.text[:500]}")
    except requests.RequestException as e:
        print(f"  ! nicht abrufbar: {e}")

    print(f"\n=== 3. POST {base}/chat/completions (Mini-Prompt) ===")
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": "Du antwortest knapp."},
            {"role": "user", "content": "Sag genau: OK"},
        ],
        "temperature": 0.7,
        "top_p": 0.95,
        "max_tokens": 16,
    }
    try:
        r = requests.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=60)
        print(f"  HTTP {r.status_code}")
        if r.ok:
            data = r.json()
            print(f"  ✓ Antwort: {data['choices'][0]['message']['content']!r}")
            print(f"  usage: {data.get('usage')}")
            return 0
        try:
            print("  Body:\n" + json.dumps(r.json(), indent=2, ensure_ascii=False)[:2000])
        except Exception:
            print("  Body:\n" + r.text[:2000])
        return 1
    except requests.RequestException as e:
        print(f"  ✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
