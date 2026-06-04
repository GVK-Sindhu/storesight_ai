#!/bin/bash

echo "=========================================================="
echo "           StoreSight AI - Video Ingestion Pipeline"
echo "=========================================================="

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "Project root detected: $PROJECT_ROOT"

# Ensure Python is installed
if ! command -v python &> /dev/null
then
    echo "Error: python could not be found. Please install Python 3.10+."
    exit 1
fi

echo "Running CV pipeline on CCTV clips..."
python "$SCRIPT_DIR/emit.py" "$@"

if [ $? -eq 0 ]; then
    echo "Pipeline executed successfully! Events generated at data/events.jsonl."
else
    echo "Error: Pipeline execution failed."
    exit 1
fi
