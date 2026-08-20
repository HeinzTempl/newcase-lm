# newcase-lm — example environment profiles
# =============================================
# Source one of these (or copy the exports into ~/.zshrc) before running
# the pipeline. Two functions = two profiles you can switch per shell:
#
#     source env.example.sh
#     newcase-local     # everything on this machine
#     newcase-cloud     # briefings via an OpenAI-compatible cloud API
#
# The identifier candidate search (NEWCASE_ANON_*) stays LOCAL in both
# profiles — it is the one stage that reads cleartext in deterministic mode.

newcase-local() {
    export NEWCASE_BACKEND=ollama
    export NEWCASE_MODEL="gemma4:31b-it-q8_0"
    # Faster on Apple Silicon via a local OpenAI-compatible server instead:
    # export NEWCASE_BACKEND=openai_compat
    # export NEWCASE_API_BASE_URL=http://localhost:8080/v1
    # export NEWCASE_API_KEY=local
    # export NEWCASE_MODEL=<model-id as the server reports it>

    export NEWCASE_ANON_MODE=deterministic
    _newcase_anon_common
    echo "newcase: local profile (backend=$NEWCASE_BACKEND, model=$NEWCASE_MODEL)"
}

newcase-cloud() {
    # Only with a provider you may send case data to (e.g. ZDR agreement).
    export NEWCASE_BACKEND=openai_compat
    export NEWCASE_API_BASE_URL=https://api.mistral.ai/v1
    export NEWCASE_MODEL=mistral-large-latest
    # Put the key in your keychain or shell rc, NOT in this file:
    # export NEWCASE_API_KEY=...
    export NEWCASE_OLLAMA_TIMEOUT=300     # cloud answers fast — fail fast too

    export NEWCASE_ANON_MODE=deterministic
    _newcase_anon_common
    echo "newcase: cloud profile ($NEWCASE_API_BASE_URL, model=$NEWCASE_MODEL)"
}

_newcase_anon_common() {
    # Candidate search for deterministic anonymization — ALWAYS local.
    export NEWCASE_ANON_BACKEND=ollama
    export NEWCASE_ANON_MODEL="gemma4:31b-it-q8_0"
    # or a local OpenAI-compatible server (oMLX, LM Studio, vLLM):
    # export NEWCASE_ANON_BACKEND=openai_compat
    # export NEWCASE_ANON_BASE_URL=http://localhost:8080/v1
    # export NEWCASE_ANON_MODEL=<model-id>

    # Shared mapping directory (same JSONs other local tools may use).
    # export NEWCASE_PSEUDONYM_DIR=~/localProjects/antropic-mcp/pseudonym_maps
}
