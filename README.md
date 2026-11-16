# COBOL → Java Modernization Agent

An AI-driven code modernization pipeline: ingests legacy COBOL, migrates it
to modern Java, and proves the migration preserves business logic by
diffing behavior against the *actual running* legacy program - not just
checking that the new code compiles.

## Milestone 1 — Core loop, no LLM (done)

Before spending any API calls on generation, this milestone proves the
**validation mechanics** work end to end:

1. `samples/cobol/BonusCalc.cbl` - a real tiered employee-bonus calculator,
   compiled and run with **GnuCOBOL** to produce genuine golden output
   (`samples/cobol/golden_outputs.json`). This is not hand-computed
   "expected" data - it's the actual stdout of the actual compiled COBOL
   binary.
2. `samples/java/BonusCalc_v1_buggy.java` - a hand-written Java migration
   containing one deliberately realistic bug: the two branches of the
   senior-tier salary check got swapped. It **compiles perfectly** and
   passes 3 of 5 cases - this is the exact bug class that AST/syntax
   validation can never catch, and the reason behavioral diffing against
   real legacy output is the project's core differentiator.
3. `scripts/validate.py` - compiles the candidate Java, runs it against
   every golden case, and diffs stdout byte-for-byte. Run against the
   buggy version, it correctly fails 2/5 cases with a clear expected-vs-
   actual report. Run against `BonusCalc_v2_fixed.java` (branches
   corrected), it passes 5/5.
4. `sandbox/Dockerfile.java` + `sandbox/run_java_sandbox.sh` - the real
   locked-down execution path (`--network=none`, memory/CPU caps,
   read-only filesystem, non-root user) for when LLM-generated code is
   being executed for real, not just hand-written proof code.

### Run it yourself

```bash
# Compile & run the real COBOL to see the golden output get generated:
cd samples/cobol
cobc -x BonusCalc.cbl
echo "500000.00 12" | ./BonusCalc

# Validate the buggy migration - watch it fail on exactly the cases
# that exercise the swapped branch:
python3 scripts/validate.py samples/java/BonusCalc_v1_buggy.java \
    --golden samples/cobol/golden_outputs.json

# Validate the fixed migration - full pass:
python3 scripts/validate.py samples/java/BonusCalc_v2_fixed.java \
    --golden samples/cobol/golden_outputs.json

# Same, via the real Docker sandbox (needs Docker + built image):
docker build -f sandbox/Dockerfile.java -t cobol-modernizer-java-sandbox .
python3 scripts/validate.py samples/java/BonusCalc_v2_fixed.java \
    --golden samples/cobol/golden_outputs.json --docker
```

### What this proves

The whole point of this project is that **a successful compile is not
proof of correctness.** `BonusCalc_v1_buggy.java` is clean, readable,
idiomatic Java that any code reviewer would probably approve on sight -
and it silently pays the wrong bonus to every senior employee under the
salary threshold. Only running it against the real legacy program's
actual output catches that. Milestone 1 proves that check works, by hand,
before a single dollar is spent on an LLM call.

## Setup

```bash
# Debian/Ubuntu
sudo apt-get install gnucobol4 default-jdk-headless

# Verify
cobc --version
javac --version
```

No paid APIs, no cloud services - everything in this milestone runs
entirely local and free.

## Roadmap

- [x] **M1** — Core loop, no LLM: AST/compile validation + Docker sandbox + golden-output diffing, proven by hand
- [x] **M2** — LLM generation, single pass (Groq free tier). Attempt 1 compiled but failed 0/5: the model changed a string literal (`"SALARY="` → `"SALARY= "`) and justified it with a false claim about COBOL's DISPLAY. Business logic was correct; only behavioral diffing against real COBOL output caught it.
- [x] **M3** — Automated test generation grounded in real COBOL: an LLM proposes inputs (never sees outputs), the compiled legacy program produces the expected outputs, cases are split into dev / held-out sets, and legacy quirks (e.g. silent PIC overflow) are quarantined for a human decision
- [ ] **M4** — Self-correction loop (LangGraph) — the "1%" differentiator
- [ ] **M5** — Stress test on real COBOL samples + benchmark numbers
- [ ] **M6** — Polish + final README
