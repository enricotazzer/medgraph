#!/usr/bin/env bash
# Run a long local-LLM job in the background, against a private Ollama server.
#
#   OLLAMA_MODELS=/Volumes/T7/ollama-models scripts/dev/llm_run.sh NAME COMMAND...
#
# - The job gets its own session: closing the editor or terminal does not stop it. Jobs resume
#   from saved predictions, so an interruption costs at most the report in progress.
# - A private `ollama serve` on 127.0.0.1:$MEDGRAPH_OLLAMA_PORT (default 11435) reads models from
#   OLLAMA_MODELS explicitly, because the Ollama app's setting is lost on reboot. The job gets
#   MEDGRAPH_LLM_BASE_URL pointing at it, and the server stops when the job ends.
# - caffeinate stops idle sleep. Closing the lid still sleeps the Mac.
# Log: $MEDGRAPH_DATA_DIR/runs/logs/<time>-NAME.log, with the server's log next to it.
set -euo pipefail

port=${MEDGRAPH_OLLAMA_PORT:-11435}
url="http://127.0.0.1:$port"

if [[ "${1:-}" == --detached ]]; then
    shift
    OLLAMA_HOST="127.0.0.1:$port" ollama serve > "$LLM_RUN_LOG.ollama" 2>&1 &
    server=$!
    trap 'kill "$server" 2> /dev/null' EXIT
    for _ in $(seq 60); do curl -sf "$url/api/tags" > /dev/null && break; sleep 1; done
    echo "$(date '+%F %T') server $url, models from $OLLAMA_MODELS"
    status=0
    MEDGRAPH_LLM_BASE_URL=$url caffeinate -i "$@" || status=$?
    echo "$(date '+%F %T') finished with exit status $status"
    exit "$status"
fi

usage="usage: OLLAMA_MODELS=<model folder> $0 NAME COMMAND..."
name=${1:?$usage}
shift
(($#)) || { echo "$usage" >&2; exit 2; }
if [[ -z "${OLLAMA_MODELS:-}" || ! -d "$OLLAMA_MODELS" ]]; then
    echo "OLLAMA_MODELS='${OLLAMA_MODELS:-}' is not a folder (is the drive mounted?)" >&2
    exit 1
fi
if curl -sf "$url/api/tags" > /dev/null; then
    echo "something already serves $url: is another run going?" >&2
    exit 1
fi
runs=$(uv run python -c 'from medgraph.settings import Settings; print(Settings().runs_dir)')
mkdir -p "$runs/logs"
LLM_RUN_LOG="$runs/logs/$(date +%Y%m%d-%H%M%S)-$name.log"
export LLM_RUN_LOG OLLAMA_MODELS
# setsid: a new session, outside the caller's process group.
nohup python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \
    "$0" --detached "$@" > "$LLM_RUN_LOG" 2>&1 < /dev/null &
echo "started (pid $!); log: $LLM_RUN_LOG"
