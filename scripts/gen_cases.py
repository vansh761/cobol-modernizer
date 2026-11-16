#!/usr/bin/env python3
"""
M3: automated test-case generation grounded in the REAL legacy program.

Pipeline:
  1. An LLM reads the COBOL source and proposes input lines aimed at every
     branch, every threshold (just below / at / just above), and rounding.
     It never sees any program output.
  2. Candidates are filtered: wrong shape (vs. the hand-checked seed inputs),
     non-printable, duplicates.
  3. Every surviving input is run through the actual compiled COBOL binary
     (GnuCOBOL). Whatever the legacy program prints IS the expected output.
     The LLM cannot influence expected values.
  4. Cases are split deterministically into DEV (may be shown to a repair
     loop as feedback) and HOLDOUT (never shown; used only for the final
     verdict), so a self-correcting model can't just memorize test answers.
  5. Inputs you declare with --quirk (e.g. inputs that overflow a PIC field
     and trigger silent COBOL truncation) are run through COBOL too, but kept
     in a separate golden_quirks.json for a human decision, not in pass/fail.

Usage:
  python3 scripts/gen_cases.py samples/cobol/BonusCalc.cbl
  python3 scripts/gen_cases.py samples/cobol/BonusCalc.cbl \\
      --candidates-file samples/cobol/candidates.json --quirk "9999999.99 15"
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import generate  # noqa: E402  (reuses the Groq client: key handling, retries)

SYSTEM_PROMPT = """You are a test engineer designing inputs for a legacy COBOL program that will be migrated to Java.

Reply with ONLY a JSON array (no markdown, no commentary). Each element is an object:
  {"input": "<one line exactly as the program reads it from standard input>", "purpose": "<5-10 words>"}

Requirements:
- Cover EVERY branch of every IF/EVALUATE in the program.
- For every numeric threshold the code compares against, include a value just below it, exactly at it, and just above it (these catch >= versus > mistakes).
- Include rounding-sensitive values: results that need more decimal places than the PIC clause allows, including ones ending in exactly 5 at the rounding digit.
- Include a minimum value and a few typical values.
- Stay inside the declared PIC capacities: every input AND every value the program computes from it must fit its field. No overflow, no negative numbers, no malformed or missing fields.
- Use exactly the same input format as the examples given."""

USER_PROMPT = """COBOL program:

{cobol_source}

Input format examples (inputs only):
{examples}

Produce {n} diverse test inputs."""


def norm(line: str) -> str:
    return " ".join(line.split())


def shape(line: str) -> tuple:
    """True per token that looks like a plain number. 'abc 5' and '50000.00'
    have different shapes than the seed '50000.00 01'."""
    return tuple(bool(re.fullmatch(r"\d+(\.\d+)?", t)) for t in line.split())


def parse_candidates(text: str):
    """Extract [{'input', 'purpose'}, ...] from a model reply. Returns
    (candidates, dropped) where dropped is a list of (raw, reason)."""
    t = text.strip()
    m = re.search(r"```(?:json)?[ \t]*\n(.*?)```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("["), t.rfind("]")
        if a == -1 or b <= a:
            raise ValueError("no JSON array found in the model reply")
        data = json.loads(t[a:b + 1])
    if not isinstance(data, list):
        raise ValueError("model reply is not a JSON array")
    out, dropped = [], []
    for item in data:
        if isinstance(item, str):
            out.append({"input": item, "purpose": ""})
        elif isinstance(item, dict) and isinstance(item.get("input"), str):
            out.append({"input": item["input"], "purpose": str(item.get("purpose", ""))})
        else:
            dropped.append((json.dumps(item)[:80], "malformed entry (no string 'input')"))
    return out, dropped


def compile_cobol(cobol_file: Path, workdir: Path) -> Path:
    exe = workdir / "legacy_prog"
    r = subprocess.run(["cobc", "-x", "-o", str(exe), str(cobol_file)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit(f"cobc failed to compile {cobol_file}:\n{r.stderr}")
    return exe


def run_cobol(exe: Path, line: str):
    """Run the real legacy binary. Returns stdout, or None if it failed."""
    try:
        r = subprocess.run([str(exe)], input=line + "\n", capture_output=True,
                           text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return r.stdout.strip()


def split_dev_holdout(cases: list):
    """Deterministic and balanced: order by hash of the input, alternate."""
    ordered = sorted(cases, key=lambda c: hashlib.sha1(norm(c["input"]).encode()).hexdigest())
    return ordered[0::2], ordered[1::2]


def build(cobol_file: Path, seed_file: Path, candidates: list, quirk_inputs: list, workdir: Path):
    seed = json.loads(seed_file.read_text())["cases"]
    seed_shapes = {shape(c["input"]) for c in seed}
    exe = compile_cobol(cobol_file, workdir)
    quirks = {norm(q) for q in quirk_inputs}
    dropped, seen, pool = [], set(), []

    def add(origin, cid, line, purpose):
        key = norm(line)
        if key in quirks:
            return                      # handled separately below
        if key in seen:
            dropped.append((line, "duplicate input")); return
        if not re.fullmatch(r"[\x20-\x7e]{1,80}", line):
            dropped.append((line, "not printable ASCII / too long")); return
        if origin == "llm" and shape(line) not in seed_shapes:
            dropped.append((line, "wrong shape vs. seed inputs")); return
        out = run_cobol(exe, key)
        if out is None:
            dropped.append((line, "legacy program failed or printed nothing")); return
        seen.add(key)
        pool.append({"id": cid, "origin": origin, "input": key,
                     "purpose": purpose, "expected_stdout": out})

    for c in seed:
        add("seed", f"seed_{c['id']}", c["input"], c.get("why_this_matters", "hand-checked seed case")[:80])
    n = 0
    for c in candidates:
        n += 1
        add("llm", f"gen_{n:03d}", c["input"], c["purpose"])

    # sanity: seeds recomputed through the binary must equal the stored golden values
    stored = {norm(c["input"]): c["expected_stdout"].strip() for c in seed}
    for c in pool:
        if c["origin"] == "seed" and stored.get(c["input"]) != c["expected_stdout"]:
            sys.exit(f"Seed case {c['id']} no longer matches stored golden output. "
                     "Your cobc build behaves differently from the one that produced the seed file.")

    quirk_cases = []
    for i, q in enumerate(sorted(quirks), 1):
        out = run_cobol(exe, q)
        quirk_cases.append({"id": f"quirk_{i:02d}", "origin": "quirk", "input": q,
                            "purpose": "declared legacy quirk (needs a human decision)",
                            "expected_stdout": out if out is not None else ""})
    dev, holdout = split_dev_holdout(pool)
    return dev, holdout, quirk_cases, dropped


def write_set(path: Path, program: str, kind: str, cases: list, note: str):
    path.write_text(json.dumps({"_comment": note, "source_program": program,
                                "set": kind, "cases": cases}, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description="Generate golden test sets from real COBOL behavior")
    ap.add_argument("cobol_file", type=Path)
    ap.add_argument("--seed", type=Path, default=Path("samples/cobol/golden_outputs.json"))
    ap.add_argument("--n", type=int, default=20, help="how many candidate inputs to ask the LLM for")
    ap.add_argument("--out-dir", type=Path, default=Path("samples/cobol"))
    ap.add_argument("--quirk", action="append", default=[], metavar="INPUT",
                    help='input line to quarantine as a legacy quirk, e.g. --quirk "9999999.99 15"')
    ap.add_argument("--candidates-file", type=Path,
                    help="reuse a saved candidates.json instead of calling the LLM (no API usage)")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    model = None
    if args.candidates_file:
        candidates, bad = parse_candidates(args.candidates_file.read_text())
    else:
        key = generate.get_api_key()
        model = os.environ.get("GROQ_MODEL", generate.DEFAULT_MODEL)
        seed_inputs = [c["input"] for c in json.loads(args.seed.read_text())["cases"]]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT.format(
                cobol_source=args.cobol_file.read_text(),
                examples="\n".join(seed_inputs), n=args.n)},
        ]
        print(f"Asking {model} for {args.n} candidate inputs ...")
        resp = generate.call_groq(messages, model, key, max_tokens=6000)
        choice = resp["choices"][0]
        if choice.get("finish_reason") == "length":
            sys.exit("The model's reply was cut off (token limit). Lower --n and retry.")
        raw = choice["message"]["content"] or ""
        try:
            candidates, bad = parse_candidates(raw)
        except ValueError as e:
            (args.out_dir / "candidates_raw.txt").write_text(raw)
            sys.exit(f"Could not parse the model reply ({e}). Raw reply saved to candidates_raw.txt.")
        (args.out_dir / "candidates.json").write_text(
            json.dumps(candidates, indent=2) + "\n")
        print(f"  saved {len(candidates)} candidates to {args.out_dir / 'candidates.json'} "
              "(rerun with --candidates-file to reuse them without API calls)")

    with tempfile.TemporaryDirectory() as tmp:
        dev, holdout, quirks, dropped = build(args.cobol_file, args.seed, candidates,
                                              args.quirk, Path(tmp))
    dropped = [(r, why) for r, why in bad] + dropped

    prog = args.cobol_file.name
    note = ("Expected outputs are the real stdout of the compiled COBOL program (GnuCOBOL). "
            "Inputs come from hand-checked seeds plus LLM proposals; the LLM never saw outputs.")
    write_set(args.out_dir / "golden_dev.json", prog, "dev", dev, note)
    write_set(args.out_dir / "golden_holdout.json", prog, "holdout", holdout,
              note + " HOLDOUT: never show these to a repair loop.")
    if quirks:
        write_set(args.out_dir / "golden_quirks.json", prog, "quirks", quirks,
                  "Declared legacy quirks (e.g. silent PIC overflow). Needs a human decision: "
                  "preserve the legacy behavior or fix it.")
    (args.out_dir / "cases_report.json").write_text(json.dumps({
        "model": model, "candidates_received": len(candidates),
        "dev": len(dev), "holdout": len(holdout), "quirks": len(quirks),
        "dropped": [{"input": r, "reason": why} for r, why in dropped]}, indent=2) + "\n")

    print(f"\nKept {len(dev) + len(holdout)} cases: {len(dev)} dev / {len(holdout)} holdout"
          f"  (+ {len(quirks)} quarantined quirks, {len(dropped)} dropped)\n")
    print(f"{'set':<8} {'origin':<6} {'input':<18} expected output (real COBOL)")
    for kind, cases in (("dev", dev), ("holdout", holdout), ("quirk", quirks)):
        for c in cases:
            print(f"{kind:<8} {c['origin']:<6} {c['input']:<18} "
                  + " | ".join(" ".join(l.split()) for l in c["expected_stdout"].splitlines()))
    if dropped:
        print("\nDropped:")
        for r, why in dropped:
            print(f"  {r!r:<28} {why}")


if __name__ == "__main__":
    main()
