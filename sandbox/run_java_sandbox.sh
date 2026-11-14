#!/usr/bin/env bash
# Compiles and runs ONE Java file inside a locked-down Docker container,
# feeding it one stdin line per line of an inputs file. Compile happens
# inside the container too, so untrusted code never touches the host JDK.
#
# Usage: ./run_java_sandbox.sh <path-to-java-file> <path-to-inputs-file>
#
# Lockdown flags:
#   --network=none   no network at all
#   --memory=512m    hard memory cap (256m was too tight for javac)
#   --cpus=1         hard CPU cap (0.5 made javac take ~45s on WSL2)
#   --read-only      read-only root filesystem
#   --tmpfs /tmp     writable scratch space that vanishes on exit
#   -u sandbox       non-root user
#   source + inputs + inner_run.sh are all mounted read-only

set -uo pipefail

JAVA_FILE="$1"
INPUTS_FILE="$2"
CLASS_NAME="$(basename "$JAVA_FILE" .java)"
SRC_DIR="$(cd "$(dirname "$JAVA_FILE")" && pwd)"
IN_DIR="$(cd "$(dirname "$INPUTS_FILE")" && pwd)"
IN_NAME="$(basename "$INPUTS_FILE")"
SANDBOX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="cobol-modernizer-java-sandbox"

docker run --rm \
    --network=none \
    --memory=512m \
    --cpus=1 \
    --read-only \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    --pids-limit=256 \
    --tmpfs /tmp \
    -u sandbox \
    -e CLASS_NAME="$CLASS_NAME" \
    -e INPUTS_FILE="/work/in/${IN_NAME}" \
    -v "${SRC_DIR}:/work/src:ro" \
    -v "${IN_DIR}:/work/in:ro" \
    -v "${SANDBOX_DIR}:/work/sandbox:ro" \
    -w /tmp \
    "$IMAGE" \
    sh /work/sandbox/inner_run.sh
