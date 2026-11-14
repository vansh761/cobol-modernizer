#!/bin/sh
# Runs INSIDE the sandbox container (mounted read-only by run_java_sandbox.sh).
# Compiles the Java file ONCE, then runs it once per input line.
#
# Output protocol (parsed by scripts/validate.py):
#   COMPILE_FAILED ...   javac error text follows, nothing else is run
#   COMPILE_OK
#   <program stdout>
#   ---STDERR---
#   <program stderr>
#   RC=<exit code>
#   ===CASE_END===        (repeated once per input line)
#
# Env: CLASS_NAME (required). SRC_DIR / INPUTS_FILE / WORKDIR default to the
# container paths and are overridable so this can be tested without Docker.

SRC_DIR="${SRC_DIR:-/work/src}"
INPUTS_FILE="${INPUTS_FILE:-/work/in/inputs.txt}"
WORKDIR="${WORKDIR:-/tmp}"

cd "$WORKDIR" || { echo "COMPILE_FAILED cannot enter $WORKDIR"; exit 0; }

if ! cp "$SRC_DIR/$CLASS_NAME.java" .; then
    echo "COMPILE_FAILED cannot copy $CLASS_NAME.java into sandbox"
    exit 0
fi

timeout 90 javac "$CLASS_NAME.java" 2> javac_err.txt
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "COMPILE_FAILED javac exit code $rc (124 = timed out)"
    cat javac_err.txt
    exit 0
fi
echo "COMPILE_OK"

while IFS= read -r line; do
    printf '%s\n' "$line" | timeout 20 java "$CLASS_NAME" 2> case_err.txt
    rc=$?
    echo "---STDERR---"
    cat case_err.txt
    echo "RC=$rc"
    echo "===CASE_END==="
done < "$INPUTS_FILE"
