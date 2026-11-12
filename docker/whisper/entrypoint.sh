#!/bin/sh
# Start whisper.cpp server with OpenAI Whisper base.en (GGML), downloading once into /models.
set -eu

MODEL_NAME="${WHISPER_MODEL:-base.en}"
MODEL_PATH="${WHISPER_MODEL_PATH:-/models/ggml-${MODEL_NAME}.bin}"
THREADS="${WHISPER_THREADS:-2}"
HOST="${WHISPER_HOST:-0.0.0.0}"
PORT="${WHISPER_PORT:-8080}"

mkdir -p "$(dirname "$MODEL_PATH")"

if [ ! -f "$MODEL_PATH" ]; then
  echo "whisper: downloading ggml model ${MODEL_NAME} into $(dirname "$MODEL_PATH")"
  download-ggml-model.sh "$MODEL_NAME" "$(dirname "$MODEL_PATH")"
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "whisper: model file missing at ${MODEL_PATH}" >&2
  exit 1
fi

echo "whisper: starting whisper-server model=${MODEL_PATH} threads=${THREADS} lang=en convert=on gpu=off"
exec whisper-server \
  --host "$HOST" \
  --port "$PORT" \
  -m "$MODEL_PATH" \
  -t "$THREADS" \
  -l en \
  --convert \
  --no-gpu
