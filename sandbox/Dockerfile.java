# Minimal, locked-down sandbox for compiling and running LLM-generated Java.
# Build once:   docker build -f sandbox/Dockerfile.java -t cobol-modernizer-java-sandbox .
# Run a file:   see sandbox/run_java_sandbox.sh - it wires the --network=none,
#               --memory, --cpus, and read-only mount flags around this image.

FROM eclipse-temurin:21-jdk-alpine

# Non-root user - never run LLM-generated code as root, even in a container.
RUN addgroup -S sandbox && adduser -S sandbox -G sandbox
WORKDIR /work
USER sandbox

# No ENTRYPOINT/CMD baked in on purpose: run_java_sandbox.sh decides exactly
# what to execute per-invocation (compile step, then run step, then diff).
