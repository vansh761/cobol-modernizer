#!/usr/bin/env python3
"""
M1 validation engine: compiles a candidate Java migration, runs it against
every golden test case (captured from actually executing the real COBOL
binary), and diffs stdout byte-for-byte.

This is deliberately the ONE piece of the whole project that must never lie:
everything downstream (the self-correction loop in M4, the benchmark numbers
in M5) depends on this function returning an honest, reproducible verdict.

Sandbox execution is pluggable: `run_docker_batch()` is the real path
(what actually ships - locked-down container, no network, resource caps),
`run_local_subprocess()` is a fallback used only where Docker isn't
available (e.g. this dev/proof environment). Swap via --local flag.
Same interface either way, so nothing above this function needs to know
or care which one ran.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaseResult:
    case_id: str
    compiled: bool
    ran: bool
    matched: bool
    actual_stdout: str = ""
    expected_stdout: str = ""
    error: str = ""


@dataclass
class ValidationReport:
    java_file: str
    compile_ok: bool
    compile_error: str
    case_results: list = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.compile_ok and all(c.matched for c in self.case_results)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.case_results if c.matched)

    def to_dict(self) -> dict:
        return {
            "java_file": self.java_file,
            "compile_ok": self.compile_ok,
            "compile_error": self.compile_error,
            "pass_count": self.pass_count,
            "total_cases": len(self.case_results),
            "all_passed": self.all_passed,
            "cases": [vars(c) for c in self.case_results],
        }


def compile_java(java_file: Path, workdir: Path) -> tuple[bool, str]:
    """Host-side javac. Used only in --local mode. In --docker mode the
    compile happens INSIDE the container (see sandbox/inner_run.sh)."""
    try:
        result = subprocess.run(
            ["javac", "-d", str(workdir), str(java_file)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return False, "javac timed out after 60s"
    return result.returncode == 0, result.stderr


def run_local_subprocess(class_name: str, workdir: Path, stdin_input: str) -> tuple[bool, str, str]:
    """Fallback path with no Docker available. NOT what ships to
    production - see run_java_sandbox.sh for the real locked-down
    container invocation. This exists only so the diff LOGIC can be
    proven in environments without Docker installed."""
    try:
        result = subprocess.run(
            ["java", "-cp", str(workdir), class_name],
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return True, result.stdout.strip(), result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "TIMEOUT: execution exceeded 10s limit"


CASE_END = "===CASE_END==="


def parse_batch_output(stdout: str, stderr: str, n_cases: int):
    """Parse the protocol printed by sandbox/inner_run.sh.
    Returns (compile_ok, compile_error, [(ran, stdout, stderr), ...])."""
    lines = stdout.splitlines()
    if not lines:
        return False, f"sandbox produced no output. docker stderr: {stderr.strip()}", []
    if lines[0].startswith("COMPILE_FAILED"):
        return False, "\n".join(lines).strip(), []
    if lines[0] != "COMPILE_OK":
        return False, f"unexpected sandbox output: {stdout[:300]!r} {stderr.strip()}", []

    results, out, err, mode, rc = [], [], [], "out", None
    for line in lines[1:]:
        if line == CASE_END:
            note = "" if rc == 0 else f"exit code {rc} (124 = timed out)"
            err_text = "\n".join(err).strip()
            results.append((rc == 0, "\n".join(out).strip(),
                            (err_text + " " + note).strip()))
            out, err, mode, rc = [], [], "out", None
        elif line == "---STDERR---":
            mode = "err"
        elif line.startswith("RC=") and mode == "err":
            rc = int(line[3:])
        else:
            (out if mode == "out" else err).append(line)

    while len(results) < n_cases:  # sandbox died early: missing cases = failures
        detail = stderr.strip()[:400]
        results.append((False, "", "no result from sandbox (killed or timed out)"
                        + (f". sandbox stderr: {detail}" if detail else "")))
    return True, "", results


def run_docker_batch(java_file: Path, inputs: list, timeout: int = 240):
    """Real path: ONE container per file. Compile once, run every input."""
    script = Path(__file__).parent.parent / "sandbox" / "run_java_sandbox.sh"
    with tempfile.TemporaryDirectory() as tmp:
        inputs_file = Path(tmp) / "inputs.txt"
        inputs_file.write_text("\n".join(inputs) + "\n")
        # The container runs as a non-root user (uid != yours). Python creates
        # temp dirs as 0700, which that user cannot read, so open them up.
        os.chmod(tmp, 0o755)
        os.chmod(inputs_file, 0o644)
        try:
            result = subprocess.run(
                ["bash", str(script), str(java_file), str(inputs_file)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"TIMEOUT: sandbox exceeded {timeout}s overall", []
    return parse_batch_output(result.stdout, result.stderr, len(inputs))


def validate(java_file: Path, golden_path: Path, use_docker: bool) -> ValidationReport:
    golden = json.loads(golden_path.read_text())
    cases = golden["cases"]
    class_name = java_file.stem

    if use_docker:
        compile_ok, compile_error, results = run_docker_batch(
            java_file, [c["input"] for c in cases])
    else:
        workdir = Path(tempfile.mkdtemp(prefix="cobol_modernizer_validate_"))
        compile_ok, compile_error = compile_java(java_file, workdir)
        results = []
        if compile_ok:
            for case in cases:
                results.append(run_local_subprocess(class_name, workdir, case["input"]))

    report = ValidationReport(
        java_file=str(java_file), compile_ok=compile_ok, compile_error=compile_error
    )

    if not compile_ok:
        for case in cases:
            report.case_results.append(
                CaseResult(case_id=case["id"], compiled=False, ran=False, matched=False,
                           error="compile failed, case not run"))
        return report

    for case, (ran, actual_stdout, err) in zip(cases, results):
        expected = case["expected_stdout"].strip()
        matched = ran and actual_stdout.strip() == expected
        report.case_results.append(
            CaseResult(case_id=case["id"], compiled=True, ran=ran, matched=matched,
                       actual_stdout=actual_stdout, expected_stdout=expected,
                       error=err if not matched else ""))
    return report


def print_report(report: ValidationReport):
    status = "✅ PASS" if report.all_passed else "❌ FAIL"
    print(f"\n{status}  {report.java_file}")
    print(f"Compile: {'OK' if report.compile_ok else 'FAILED'}")
    if not report.compile_ok:
        print(f"  {report.compile_error}")
        return
    print(f"Cases: {report.pass_count}/{len(report.case_results)} matched golden output\n")
    for c in report.case_results:
        mark = "✓" if c.matched else "✗"
        print(f"  [{mark}] {c.case_id}")
        if not c.matched:
            print(f"      expected: {c.expected_stdout!r}")
            print(f"      actual:   {c.actual_stdout!r}")
            if c.error:
                print(f"      error:    {c.error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate a migrated Java file against golden COBOL output")
    parser.add_argument("java_file", type=Path)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--docker", action="store_true", help="Use the real Docker sandbox (requires Docker)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable report")
    args = parser.parse_args()

    report = validate(args.java_file, args.golden, use_docker=args.docker)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report)

    sys.exit(0 if report.all_passed else 1)
