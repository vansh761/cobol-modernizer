#!/usr/bin/env python3
"""
M2: ask an LLM (Groq, free tier) to migrate a COBOL program to Java, save the
result, and optionally run it through the same golden-output validator as M1.

Design rules that keep the benchmark honest:
  * The prompt contains ONLY the COBOL source and generic migration rules.
    It never contains golden outputs or test expectations. The LLM must
    earn its pass from the source alone.
  * Everything the model returns is untrusted. It is saved as text and only
    ever executed by validate.py inside the Docker sandbox.
  * The API key is read from the GROQ_API_KEY environment variable and is
    never printed, logged, or written to any file.

Usage:
  python3 scripts/generate.py --list-models
  python3 scripts/generate.py samples/cobol/BonusCalc.cbl --validate
  GROQ_MODEL=openai/gpt-oss-120b python3 scripts/generate.py samples/cobol/BonusCalc.cbl --attempt 2 --validate
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"  # override with GROQ_MODEL; see --list-models

SYSTEM_PROMPT = """You are an expert mainframe-modernization engineer migrating COBOL to Java 21.

Rules:
- Output ONE complete, compilable Java source file and nothing else: no explanations, no markdown fences.
- The file must declare `public class {class_name}` with `public static void main(String[] args)`. No package statement. Standard library only.
- Preserve the observable behavior of the COBOL program EXACTLY: same inputs, same outputs, same rounding, same branch logic. Do not "improve" or simplify the business logic.
- ACCEPT ... FROM CONSOLE means: read one line from standard input. DISPLAY means: print a line to standard output.
- Use java.math.BigDecimal for decimal arithmetic. Respect COBOL ROUNDED semantics and the number of decimal places in each PIC clause.
- Numeric-edited PIC clauses (e.g. Z, 9, '.') control the exact printed text. Reproduce their total width, leading-space padding, and decimal places precisely.
- Preserve every IF/ELSE branch, including nested conditions and the exact comparison operators."""

USER_PROMPT = """Migrate this COBOL program to Java 21 as `{class_name}`:

{cobol_source}
"""


def get_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        sys.exit("GROQ_API_KEY is not set. Export it in your shell (never paste it into a file).")
    return key


def _request(path: str, key: str, payload: dict | None = None, timeout: int = 90) -> dict:
    # A custom User-Agent matters: Groq sits behind a CDN that rejects the
    # default "Python-urllib" agent with a 403.
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "cobol-modernizer/0.2",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers,
                                 method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def call_groq(messages: list, model: str, key: str, max_retries: int = 5, max_tokens: int = 4096) -> dict:
    """Chat completion with backoff on 429 (the free tier rate-limits hard)."""
    payload = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": max_tokens}
    delay = 2.0
    for attempt in range(1, max_retries + 1):
        try:
            return _request("/chat/completions", key, payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:500]
            if e.code == 429 and attempt < max_retries:
                wait = float(e.headers.get("Retry-After", delay)) if e.headers else delay
                wait = min(max(wait, 1.0), 60.0)
                print(f"  rate limited (429); waiting {wait:.0f}s before retry {attempt}/{max_retries - 1}",
                      file=sys.stderr)
                time.sleep(wait)
                delay = min(delay * 2, 60.0)
                continue
            if e.code == 429:
                sys.exit(f"Groq is still rate-limiting after {max_retries} attempts (free-tier limit). "
                         "Wait a minute and rerun, or try another model via GROQ_MODEL.")
            if e.code == 401:
                sys.exit("Groq rejected the API key (401). Regenerate it at console.groq.com/keys.")
            if e.code in (400, 404):
                sys.exit(f"Groq says the request/model is invalid ({e.code}): {body}\n"
                         f"Model used: {model}. Run with --list-models and set GROQ_MODEL.")
            sys.exit(f"Groq API error {e.code}: {body}")
        except urllib.error.URLError as e:
            sys.exit(f"Network error talking to Groq: {e.reason}")
    sys.exit("Gave up: no attempts were made (max_retries must be >= 1).")


def list_models(key: str):
    data = _request("/models", key)
    ids = sorted(m["id"] for m in data.get("data", []))
    print(f"{len(ids)} models available to your key:")
    for i in ids:
        print("  ", i)


def extract_java(text: str) -> str:
    """Pull compilable Java out of a model reply: fenced block if present,
    otherwise everything from the first import/class line."""
    blocks = re.findall(r"```(?:java)?[ \t]*\n(.*?)```", text, re.DOTALL)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    m = re.search(r"^(import |public class |class )", text, re.MULTILINE)
    return (text[m.start():] if m else text).strip() + "\n"


def normalize_java(code: str, class_name: str):
    """Drop `package` lines (the sandbox compiles in the default package) and
    force the public class name to match the filename. Returns (code, notes)."""
    notes = []
    if re.search(r"^\s*package\s+[\w.]+\s*;", code, re.MULTILINE):
        code = re.sub(r"^\s*package\s+[\w.]+\s*;\s*\n?", "", code, flags=re.MULTILINE)
        notes.append("removed package statement")
    m = re.search(r"public\s+(?:final\s+)?class\s+(\w+)", code)
    if m and m.group(1) != class_name:
        old = m.group(1)
        code = re.sub(rf"\b{re.escape(old)}\b", class_name, code)
        notes.append(f"renamed class {old} -> {class_name}")
    return code, notes


def generate(cobol_path: Path, out_path: Path, model: str, key: str) -> dict:
    class_name = out_path.stem
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{class_name}", class_name)},
        {"role": "user", "content": USER_PROMPT.format(class_name=class_name,
                                                        cobol_source=cobol_path.read_text())},
    ]
    t0 = time.time()
    resp = call_groq(messages, model, key)
    latency = time.time() - t0

    raw = resp["choices"][0]["message"]["content"] or ""
    code, notes = normalize_java(extract_java(raw), class_name)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code)
    usage = resp.get("usage", {})
    meta = {
        "source": str(cobol_path), "output": str(out_path), "model": model,
        "latency_s": round(latency, 2), "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"), "normalizations": notes,
    }
    out_path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main():
    ap = argparse.ArgumentParser(description="Migrate COBOL to Java with an LLM (Groq)")
    ap.add_argument("cobol_file", type=Path, nargs="?")
    ap.add_argument("--attempt", type=int, default=1, help="attempt number, used in the output filename")
    ap.add_argument("--out-dir", type=Path, default=Path("samples/java/generated"))
    ap.add_argument("--golden", type=Path, default=Path("samples/cobol/golden_outputs.json"))
    ap.add_argument("--validate", action="store_true", help="run the generated Java through validate.py")
    ap.add_argument("--local", action="store_true", help="with --validate: use host javac instead of Docker (dev only)")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args()

    key = get_api_key()
    if args.list_models:
        return list_models(key)
    if not args.cobol_file:
        ap.error("cobol_file is required unless --list-models is used")

    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    out_path = args.out_dir / f"{args.cobol_file.stem}_llm_a{args.attempt}.java"
    print(f"Generating {out_path} with {model} ...")
    meta = generate(args.cobol_file, out_path, model, key)
    print(f"  done in {meta['latency_s']}s, tokens: {meta['prompt_tokens']} in / {meta['completion_tokens']} out")
    for n in meta["normalizations"]:
        print(f"  note: {n}")

    if args.validate:
        sys.path.insert(0, str(Path(__file__).parent))
        import validate
        report = validate.validate(out_path, args.golden, use_docker=not args.local)
        validate.print_report(report)
        sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
