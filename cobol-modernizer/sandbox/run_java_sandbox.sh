#!/usr/bin/env bash
# Compiles and runs a single Java file inside a locked-down Docker sandbox,
# feeding it one line of stdin input, and printing whatever the program
# prints to stdout/stderr.
#
# Usage: ./run_java_sandbox.sh <path-to-java-file> "<stdin-input-line>"
#
# Lockdown flags explained:
#   --network=none     no network access at all - LLM-generated code cannot
#                       phone home, hit an API, or exfiltrate anything
#   --memory=256m       hard memory cap
#   --cpus=0.5          hard CPU cap - one runaway loop can't starve the host
#   --read-only         container filesystem is read-only except /tmp
#   --tmpfs /tmp        writable scratch space that vanishes on exit
#   -u sandbox           non-root user (baked into the image, reasserted here)
#   timeout 10s          hard wall-clock kill switch, belt-and-suspenders
#                        alongside Docker's own controls

set -euo pipefail

JAVA_FILE="$1"
STDIN_INPUT="${2:-}"
CLASS_NAME="$(basename "$JAVA_FILE" .java)"
FILE_DIR="$(cd "$(dirname "$JAVA_FILE")" && pwd)"
IMAGE="cobol-modernizer-java-sandbox"

echo "$STDIN_INPUT" | docker run --rm -i \
    --network=none \
    --memory=256m \
    --cpus=0.5 \
    --read-only \
    --tmpfs /tmp \
    -u sandbox \
    -v "${FILE_DIR}:/work/src:ro" \
    -w /tmp \
    "$IMAGE" \
    sh -c "cp /work/src/${CLASS_NAME}.java . && \
           timeout 10s javac ${CLASS_NAME}.java && \
           timeout 10s java ${CLASS_NAME}"
