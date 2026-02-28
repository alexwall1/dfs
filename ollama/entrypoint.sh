#!/bin/bash
set -e

# Start Ollama server in background
ollama serve &
PID=$!

# Wait until the API is responsive
echo "Waiting for Ollama to be ready..."
until ollama list > /dev/null 2>&1; do
    sleep 2
done
echo "Ollama is ready."

# Pull the requested model (no-op if already cached in the volume)
echo "Pulling model ${OLLAMA_MODEL}..."
ollama pull "${OLLAMA_MODEL}"
echo "Model ${OLLAMA_MODEL} is ready."

# Keep the server process in foreground
wait $PID
