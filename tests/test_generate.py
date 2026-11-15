"""Offline tests for scripts/generate.py: Groq is replaced by a fake, so no
network, no key, no cost. Verifies response cleanup, retry/backoff, error
handling, and that the prompt never leaks golden outputs to the model."""
import email.message, io, json, os, sys, tempfile, urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import generate, validate

COBOL = ROOT / "samples/cobol/BonusCalc.cbl"
GOLDEN = ROOT / "samples/cobol/golden_outputs.json"
GOOD_JAVA = (ROOT / "samples/java/BonusCalc_v2_fixed.java").read_text()
results = []

def check(name, ok):
    print(("PASS" if ok else "FAIL"), name); results.append(ok)

def api_reply(content):
    return json.dumps({"choices": [{"message": {"content": content}}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 200}}).encode()

class FakeResp:
    def __init__(self, body): self.body = body
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False

def http_error(code, body=b"{}", headers=None):
    h = email.message.Message()
    for k, v in (headers or {}).items(): h[k] = v
    return urllib.error.HTTPError("https://api.groq.com", code, "err", h, io.BytesIO(body))

os.environ["GROQ_API_KEY"] = "gsk_TESTKEY_not_real"

# 1. fenced reply with chatter, wrong class name, and a package line
messy = ("Sure! Here is the migrated code:\n```java\npackage com.example;\n"
         + GOOD_JAVA.replace("BonusCalc_v2_fixed", "BonusCalc") + "```\nHope this helps!")
sent = {}
def fake_urlopen(req, timeout=0):
    sent["payload"] = json.loads(req.data.decode()); sent["headers"] = dict(req.header_items())
    return FakeResp(api_reply(messy))
with tempfile.TemporaryDirectory() as tmp, mock.patch("urllib.request.urlopen", fake_urlopen):
    out = Path(tmp) / "BonusCalc_llm_a1.java"
    meta = generate.generate(COBOL, out, "test-model", "gsk_TESTKEY_not_real")
    code = out.read_text()
    check("fences/chatter stripped", code.startswith("import") and "Sure!" not in code and "```" not in code)
    check("package line removed", "package" not in code)
    check("class renamed to match filename", "public class BonusCalc_llm_a1" in code and any("renamed" in n for n in meta["normalizations"]))
    check("meta.json written with token counts", json.loads(out.with_suffix(".meta.json").read_text())["completion_tokens"] == 200)
    r = validate.validate(out, GOLDEN, use_docker=False)
    check("cleaned LLM output compiles and passes 5/5 golden cases", r.compile_ok and r.pass_count == 5)

# 2. prompt hygiene: COBOL source present, golden answers absent, key only in the auth header
msgs = json.dumps(sent["payload"]["messages"])
golden_text = GOLDEN.read_text()
leaks = [s for s in ("180000.00", "75000.00", "EDGE_CASE", "expected_stdout", "575000") if s in msgs]
check("prompt contains the COBOL source", "DETERMINE-BONUS-RATE" in msgs)
check("prompt leaks no golden outputs: " + (str(leaks) if leaks else "none"), not leaks)
check("API key not in message body", "TESTKEY" not in msgs)
check("custom User-Agent sent (avoids CDN 403)", sent["headers"].get("User-agent", "").startswith("cobol-modernizer"))

# 3. unfenced reply: chatter before the first import
assert generate.extract_java("Here you go:\n\nimport java.util.Scanner;\npublic class X {}").startswith("import java.util.Scanner")
check("unfenced reply: starts at first import", True)

# 4. 429 then success; honors Retry-After; sleeps are mocked
calls = {"n": 0, "sleeps": []}
def flaky(req, timeout=0):
    calls["n"] += 1
    if calls["n"] < 3: raise http_error(429, headers={"Retry-After": "7"})
    return FakeResp(api_reply("ok"))
with mock.patch("urllib.request.urlopen", flaky), mock.patch("time.sleep", lambda s: calls["sleeps"].append(s)):
    resp = generate.call_groq([{"role": "user", "content": "x"}], "m", "k")
check("retries through two 429s then succeeds", calls["n"] == 3 and resp["choices"][0]["message"]["content"] == "ok")
check("honors Retry-After header", calls["sleeps"] == [7.0, 7.0])

# 5. exhausted retries exit cleanly instead of looping forever
with mock.patch("urllib.request.urlopen", lambda r, timeout=0: (_ for _ in ()).throw(http_error(429))), \
     mock.patch("time.sleep", lambda s: None):
    try: generate.call_groq([], "m", "k", max_retries=3); ok = False
    except SystemExit as e: ok = "rate-limiting" in str(e) and "3 attempts" in str(e)
check("gives up after max retries with a clear message", ok)

# 6. error messages: 401 and bad model; neither leaks the key
for code, needle in ((401, "rejected the API key"), (404, "--list-models")):
    with mock.patch("urllib.request.urlopen", lambda r, timeout=0, c=code: (_ for _ in ()).throw(http_error(c, b'{"error":"x"}'))):
        try: generate.call_groq([], "bad-model", "gsk_SECRET"); ok = False
        except SystemExit as e: ok = needle in str(e) and "gsk_SECRET" not in str(e)
    check(f"HTTP {code}: helpful message, key not leaked", ok)

# 7. missing key
with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
    try: generate.get_api_key(); ok = False
    except SystemExit as e: ok = "GROQ_API_KEY" in str(e)
check("missing key: clear message", ok)

print("\nALL PASSED" if all(results) else "\nSOMETHING FAILED")
sys.exit(0 if all(results) else 1)
