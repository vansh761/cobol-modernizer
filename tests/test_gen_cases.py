"""Tests for scripts/gen_cases.py. The LLM is mocked; the COBOL is REAL
(GnuCOBOL must be installed), so expected outputs come from the actual
legacy binary exactly as in production use."""
import json, os, sys, tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_cases, generate, validate

COBOL = ROOT / "samples/cobol/BonusCalc.cbl"
SEED = ROOT / "samples/cobol/golden_outputs.json"
seed_cases = json.loads(SEED.read_text())["cases"]
results = []
def check(name, ok, extra=""):
    print(("PASS" if ok else "FAIL"), name, extra); results.append(bool(ok))

good = [  # valid, diverse: thresholds, rounding, typical
    ("1.00 00", "min-ish"), ("50000.00 01", "dup of a seed"), ("50000.00  01", "dup with extra spaces"),
    ("60000.00 1", "years below first threshold"), ("60000.00 2", "at 2-year threshold"),
    ("60000.00 4", "just below 5"), ("60000.00 5", "at 5-year threshold"),
    ("60000.00 9", "just below 10"), ("60000.00 10", "at 10-year threshold"),
    ("999999.99 12", "just below salary threshold"), ("1000000.00 12", "exactly at salary threshold"),
    ("1000000.01 12", "just above salary threshold"), ("33333.33 03", "rounding .6665"),
    ("12345.67 01", "rounding case"), ("55555.55 07", "rounding ends in 5"), ("100 05", "integer salary"),
    ("2500000.00 20", "senior high salary"), ("7500.50 14", "senior normal salary"),
]
bad = [("abc 5", "malformed"), ("50000.00", "missing field"), ("50000.00 1 2", "extra field"),
       ("-5.00 03", "negative"), ("50000.00 01\u00e9", "non-ascii")]
cands = [{"input": i, "purpose": p} for i, p in good + bad]
cands.append({"input": "9999999.99 15", "purpose": "maximum values (overflows TOTAL)"})
cands.append({"purpose": "entry with no input field"})          # malformed entry
cands.append("75000.00 03")                                       # plain-string entry allowed

def run_main(argv, call_groq=None):
    with mock.patch.object(sys, "argv", ["gen_cases.py"] + argv):
        if call_groq:
            with mock.patch.object(generate, "call_groq", call_groq):
                gen_cases.main()
        else:
            gen_cases.main()

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    cfile = tmp / "cands.json"; cfile.write_text(json.dumps(cands))
    out = tmp / "out"
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_main([str(COBOL), "--seed", str(SEED), "--candidates-file", str(cfile),
                  "--out-dir", str(out), "--quirk", "9999999.99 15"])
    dev = json.loads((out / "golden_dev.json").read_text())["cases"]
    hold = json.loads((out / "golden_holdout.json").read_text())["cases"]
    quirk = json.loads((out / "golden_quirks.json").read_text())["cases"]
    report = json.loads((out / "cases_report.json").read_text())
    allc = dev + hold
    inputs = [c["input"] for c in allc]

    check("no duplicate inputs across dev+holdout", len(inputs) == len(set(inputs)))
    check("whitespace-variant duplicate removed", "50000.00 01" in inputs and "50000.00  01" not in inputs
          and sum(1 for i in inputs if i == "50000.00 01") == 1)
    dropped_inputs = {d["input"] for d in report["dropped"]}
    check("malformed shapes dropped (abc / missing / extra / negative)",
          {"abc 5", "50000.00", "50000.00 1 2", "-5.00 03"} <= dropped_inputs)
    check("non-ASCII input dropped", any("\u00e9" in d for d in dropped_inputs))
    check("malformed entry (no input field) recorded", any("malformed entry" in d["reason"] for d in report["dropped"]))
    check("plain-string candidate accepted", "75000.00 03" in inputs)
    check("overflow input quarantined, not in pass/fail sets", "9999999.99 15" not in inputs and quirk[0]["input"] == "9999999.99 15")
    check("dev/holdout balanced (differ by at most 1)", abs(len(dev) - len(hold)) <= 1, f"{len(dev)}/{len(hold)}")
    check("dev and holdout are disjoint", not ({c["input"] for c in dev} & {c["input"] for c in hold}))
    check("all 5 seeds present, origin tagged", sum(1 for c in allc if c["origin"] == "seed") == 5)
    stored = {" ".join(c["input"].split()): c["expected_stdout"].strip() for c in seed_cases}
    check("seed expected outputs equal the hand-verified originals",
          all(stored[c["input"]] == c["expected_stdout"] for c in allc if c["origin"] == "seed"))
    check("rounding case came from real COBOL (1666.67)", any("1666.67" in c["expected_stdout"] for c in allc if c["input"] == "33333.33 03"))

    # determinism: same inputs -> same split
    out2 = tmp / "out2"
    with contextlib.redirect_stdout(io.StringIO()):
        run_main([str(COBOL), "--seed", str(SEED), "--candidates-file", str(cfile),
                  "--out-dir", str(out2), "--quirk", "9999999.99 15"])
    check("split is deterministic across runs",
          [c["id"] for c in json.loads((out2 / "golden_dev.json").read_text())["cases"]] == [c["id"] for c in dev])

    # integration with the validator: known-good Java passes everything; known-bad fails
    good_java = ROOT / "samples/java/BonusCalc_v2_fixed.java"
    bad_java = ROOT / "samples/java/BonusCalc_v1_buggy.java"
    rd = validate.validate(good_java, out / "golden_dev.json", use_docker=False)
    rh = validate.validate(good_java, out / "golden_holdout.json", use_docker=False)
    check("correct Java passes the whole generated dev set", rd.all_passed, f"{rd.pass_count}/{len(rd.case_results)}")
    check("correct Java passes the whole generated holdout set", rh.all_passed, f"{rh.pass_count}/{len(rh.case_results)}")
    bd = validate.validate(bad_java, out / "golden_dev.json", use_docker=False)
    bh = validate.validate(bad_java, out / "golden_holdout.json", use_docker=False)
    check("swapped-branch Java fails generated cases", not (bd.all_passed and bh.all_passed),
          f"dev {bd.pass_count}/{len(bd.case_results)}, holdout {bh.pass_count}/{len(bh.case_results)}")
    rq = validate.validate(good_java, out / "golden_quirks.json", use_docker=False)
    check("correct Java FAILS the overflow quirk (real COBOL truncates TOTAL)", not rq.all_passed)
    check("quirk golden shows the truncated total", "1199999.99" in quirk[0]["expected_stdout"])

    # LLM path: prompt hygiene + fenced JSON reply + saved candidates
    seen = {}
    def fake_call(messages, model, key, max_retries=5, max_tokens=4096):
        seen["msgs"] = json.dumps(messages); seen["max_tokens"] = max_tokens
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Here:\n```json\n" + json.dumps(cands[:6]) + "\n```"}}]}
    os.environ["GROQ_API_KEY"] = "gsk_TEST"
    out3 = tmp / "out3"
    with contextlib.redirect_stdout(io.StringIO()):
        run_main([str(COBOL), "--seed", str(SEED), "--out-dir", str(out3)], call_groq=fake_call)
    # Whole expected-output LINES must never appear (bare numbers legitimately can: a seed
    # INPUT like "75000.00 03" is shown as a format example and shares digits with outputs).
    flat = " ".join(json.loads(seen["msgs"])[0]["content"].split()) + " " + " ".join(json.loads(seen["msgs"])[1]["content"].split())
    leaks = [l for c in seed_cases for l in c["expected_stdout"].splitlines()
             if l in seen["msgs"] or " ".join(l.split()) in flat]
    leaks += [k for k in ("expected_stdout",) if k in seen["msgs"]]
    check("LLM prompt contains the COBOL source and seed inputs", "DETERMINE-BONUS-RATE" in seen["msgs"] and "500000.00 12" in seen["msgs"])
    check("LLM prompt leaks no expected outputs: " + (str(leaks) if leaks else "none"), not leaks)
    check("candidates saved for free reuse", (out3 / "candidates.json").exists())

    # truncated reply handled cleanly
    def cut(messages, model, key, max_retries=5, max_tokens=4096):
        return {"choices": [{"finish_reason": "length", "message": {"content": "[{\"input\": \"1.00"}}]}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_main([str(COBOL), "--seed", str(SEED), "--out-dir", str(tmp / "o4")], call_groq=cut); ok = False
    except SystemExit as e:
        ok = "cut off" in str(e)
    check("truncated model reply: clear message", ok)

    # unparsable reply: raw saved, clear message
    def junk(messages, model, key, max_retries=5, max_tokens=4096):
        return {"choices": [{"finish_reason": "stop", "message": {"content": "Sorry, I can't do that."}}]}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run_main([str(COBOL), "--seed", str(SEED), "--out-dir", str(tmp / "o5")], call_groq=junk); ok = False
    except SystemExit as e:
        ok = "Could not parse" in str(e) and (tmp / "o5" / "candidates_raw.txt").exists()
    check("unparsable reply: raw text saved, clear message", ok)

print("\n" + buf.getvalue())
print("ALL PASSED" if all(results) else "SOMETHING FAILED")
sys.exit(0 if all(results) else 1)
