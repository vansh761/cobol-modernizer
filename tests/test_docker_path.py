"""Tests the Docker-mode code path WITHOUT Docker: run_docker_batch is
replaced by a stand-in that runs the real sandbox/inner_run.sh locally
(same script that runs in the container), then the real parser + diff logic
run on its real output."""
import os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import validate

ROOT = Path(__file__).parent.parent
GOLDEN = ROOT / "samples/cobol/golden_outputs.json"

def fake_batch(java_file, inputs, timeout=240):
    with tempfile.TemporaryDirectory() as tmp:
        inp = Path(tmp) / "inputs.txt"
        inp.write_text("\n".join(inputs) + "\n")
        work = Path(tmp) / "work"; work.mkdir()
        env = dict(os.environ, CLASS_NAME=java_file.stem, SRC_DIR=str(java_file.parent),
                   INPUTS_FILE=str(inp), WORKDIR=str(work))
        r = subprocess.run(["sh", str(ROOT / "sandbox/inner_run.sh")],
                           capture_output=True, text=True, env=env)
    return validate.parse_batch_output(r.stdout, r.stderr, len(inputs))

validate.run_docker_batch = fake_batch

def check(name, ok):
    print(("PASS" if ok else "FAIL"), name)
    return ok

results = []
r = validate.validate(ROOT / "samples/java/BonusCalc_v2_fixed.java", GOLDEN, use_docker=True)
results.append(check("v2_fixed: compiles, 5/5 match", r.compile_ok and r.pass_count == 5))

r = validate.validate(ROOT / "samples/java/BonusCalc_v1_buggy.java", GOLDEN, use_docker=True)
failed = sorted(c.case_id for c in r.case_results if not c.matched)
results.append(check("v1_buggy: 3/5, exactly the two senior cases fail",
    r.pass_count == 3 and failed == ["tier_senior_high_salary", "tier_senior_normal_salary_EDGE_CASE"]))

with tempfile.TemporaryDirectory() as tmp:
    bad = Path(tmp) / "Broken.java"
    bad.write_text("public class Broken { this is not java }")
    r = validate.validate(bad, GOLDEN, use_docker=True)
    results.append(check("syntax error: reported as compile failure with javac text",
        (not r.compile_ok) and "error" in r.compile_error.lower()))

    slow = Path(tmp) / "Slow.java"
    slow.write_text("public class Slow { public static void main(String[] a) throws Exception { Thread.sleep(60000); } }")
    r = validate.validate(slow, GOLDEN, use_docker=True)
    results.append(check("hanging program: cases fail, validator doesn't crash",
        r.compile_ok and r.pass_count == 0))
    print("   timeout note on case 1:", r.case_results[0].error)

print("\nALL PASSED" if all(results) else "\nSOMETHING FAILED")
sys.exit(0 if all(results) else 1)
