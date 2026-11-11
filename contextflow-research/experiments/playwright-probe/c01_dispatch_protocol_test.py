#!/usr/bin/env python3
"""C-01 pure dispatch protocol tests in a real browser, without the extension.

Run from contextflow-research:
    python -B experiments/playwright-probe/c01_dispatch_protocol_test.py

The local HTTP server binds port 0 atomically. Playwright uses a disposable
browser profile; no evidence, profiles, or other repository artifacts are saved.
Identity API contract: canonicalIdentityDocument({endpointId, sessionId,
messageId, normalizedText}) returns JSON.stringify of the seven-element array.
normalizedText is already normalized by parse/normalizeFullMessage, not raw text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OPEN = "[CONTEXTFLOW_DISPATCH_V1]"
CLOSE = "[/CONTEXTFLOW_DISPATCH_V1]"


def block(body: str) -> str:
    return f"{OPEN}\n{body}\n{CLOSE}"


def none() -> dict:
    return {"status": "none", "blocks": [], "content": None, "error": None}


def valid(*blocks: str) -> dict:
    return {"status": "valid", "blocks": list(blocks),
            "content": "\n\n".join(blocks), "error": None}


def invalid(code: str) -> dict:
    # Error wording is diagnostic, not a wire contract; assert its shape below.
    return {"status": "invalid", "blocks": [], "content": None,
            "error": {"code": code}}


PARSE_CASES = [
    ("ordinary reply", "ordinary answer", none()),
    ("empty reply", "", none()),
    ("whitespace reply", " \t\r\n", none()),
    ("one Chinese block", block("请 Codex 修复 Adapter。"), valid("请 Codex 修复 Adapter。")),
    ("two blocks join with two LF", block("A") + "\ntext\n" + block("B"), valid("A", "B")),
    ("marker outer whitespace", f" \t{OPEN} \nA\n\t{CLOSE}  ", valid("A")),
    ("block outer trim only", block(" \n A  \n\n  B\t\n "), valid("A  \n\n  B")),
    ("backtick fenced example", "```text\n" + block("example") + "\n```", none()),
    ("tilde fenced example", "~~~text\n" + block("example") + "\n~~~", none()),
    ("nested opening", f"{OPEN}\n{OPEN}\nA\n{CLOSE}", invalid("NESTED_OPEN_MARKER")),
    ("missing close", f"{OPEN}\nA", invalid("UNCLOSED_BLOCK")),
    ("opening at EOF", OPEN, invalid("UNCLOSED_BLOCK")),
    ("orphan close", CLOSE, invalid("ORPHAN_CLOSE_MARKER")),
    ("empty block", block(""), invalid("EMPTY_BLOCK")),
    ("whitespace-only block", block(" \t\n\u3000"), invalid("EMPTY_BLOCK")),
    ("prefix opening", "prefix " + OPEN, invalid("AMBIGUOUS_MARKER")),
    ("suffix opening", OPEN + " suffix", invalid("AMBIGUOUS_MARKER")),
    ("prefix closing", "prefix " + CLOSE, invalid("AMBIGUOUS_MARKER")),
    ("suffix closing", CLOSE + " suffix", invalid("AMBIGUOUS_MARKER")),
    ("inline code marker", "`" + OPEN + "`", invalid("AMBIGUOUS_MARKER")),
    ("same-line pair", OPEN + CLOSE, invalid("AMBIGUOUS_MARKER")),
    ("affixed brackets", "[" + OPEN + "]", invalid("AMBIGUOUS_MARKER")),
    ("unclosed backtick fence", "```\n" + block("hidden"), none()),
    ("unclosed tilde fence", "~~~\n" + block("hidden"), none()),
    ("LF body", block("A\nB"), valid("A\nB")),
    ("CRLF body", block("A\nB").replace("\n", "\r\n"), valid("A\nB")),
    ("CR body", block("A\nB").replace("\n", "\r"), valid("A\nB")),
    ("mixed newlines", f"{OPEN}\r\nA\rB\n{CLOSE}", valid("A\nB")),
    ("four-space backticks are not fence", "    ```\n" + block("A") + "\n    ```", valid("A")),
    ("four-space tildes are not fence", "    ~~~\n" + block("A") + "\n    ~~~", valid("A")),
    ("four-space non-fence still rejects inline marker", "    ```\nprefix " + OPEN,
     invalid("AMBIGUOUS_MARKER")),
    ("short backticks are not fence", "``\n" + block("A"), valid("A")),
    ("short tildes are not fence", "~~\n" + block("A"), valid("A")),
    ("wrong close character", "```\n~~~\n" + block("hidden"), none()),
    ("wrong tilde close character", "~~~\n```\n" + block("hidden"), none()),
    ("short fence close", "````\n```\n" + block("hidden"), none()),
    ("short tilde close", "~~~~\n~~~\n" + block("hidden"), none()),
    ("longer fence close", "```\nhidden\n`````\n" + block("A"), valid("A")),
    ("longer tilde close", "~~~\nhidden\n~~~~~\n" + block("A"), valid("A")),
    ("close permits trailing spaces and tab", "```\nhidden\n   ``` \t\n" + block("A"), valid("A")),
    ("close rejects trailing text", "```\n``` trailing\n" + block("hidden"), none()),
    ("close rejects four-space indent", "```\n    ```\n" + block("hidden"), none()),
    ("backtick in info string disallows opening", "```info`bad\n" + block("A"), valid("A")),
    ("tilde info may contain backtick", "~~~info`ok\n" + block("hidden"), none()),
    ("repeated complete pairs", block("A") + "\n" + block("A"), valid("A", "A")),
    ("consecutive opening", OPEN + "\n" + OPEN, invalid("NESTED_OPEN_MARKER")),
    ("consecutive closing discards earlier block", block("A") + "\n" + CLOSE,
     invalid("ORPHAN_CLOSE_MARKER")),
    ("later unclosed discards earlier block", block("A") + "\n" + OPEN,
     invalid("UNCLOSED_BLOCK")),
    ("later empty discards earlier block", block("A") + "\n" + block(" "), invalid("EMPTY_BLOCK")),
    ("later ambiguity discards earlier block", block("A") + "\nprefix " + OPEN,
     invalid("AMBIGUOUS_MARKER")),
    ("inline marker inside body", block("A\nprefix " + CLOSE), invalid("AMBIGUOUS_MARKER")),
    ("lowercase variants", block("A").lower(), none()),
    ("different version", block("A").replace("V1", "V2"), none()),
    ("parameter variant without exact token", "[CONTEXTFLOW_DISPATCH_V1 agent=x]", none()),
    ("spaced variant without exact token", "[ CONTEXTFLOW_DISPATCH_V1 ]", none()),
    ("variant containing exact token", "[CONTEXTFLOW_DISPATCH_V1]extra", invalid("AMBIGUOUS_MARKER")),
    ("blockquote prefix is ambiguous", "> " + OPEN, invalid("AMBIGUOUS_MARKER")),
    ("lowercase closing does not close block", OPEN + "\nA\n" + CLOSE.lower(),
     invalid("UNCLOSED_BLOCK")),
    ("fenced ambiguous and orphan markers ignored", "```\nprefix " + OPEN + "\n" + CLOSE + "\n```", none()),
    ("fence ends before real block", "```\n" + block("hidden") + "\n```\n" + block("A"), valid("A")),
    ("fenced lines retained inside body", block("A\n```\n" + block("example") + "\n```\nB"),
     valid("A\n```\n" + block("example") + "\n```\nB")),
    ("unclosed fence inside block hides closing", block("A\n```\nexample"), invalid("UNCLOSED_BLOCK")),
    ("completed block followed by unclosed fence", block("A") + "\n```\n" + OPEN, valid("A")),
    ("foreign fence does not replace active opener", "````\n~~~\n```\n" + block("hidden") +
     "\n````\n" + block("A"), valid("A")),
]
for indent in range(4):
    for fence in ("```", "~~~"):
        PARSE_CASES.append((f"fence {fence} with {indent}-space indent",
                            " " * indent + fence + "\n" + block("hidden") +
                            "\n" + " " * indent + fence, none()))

# Only CR/LF delimit protocol lines. U+2028/U+2029 remain literal info text,
# not line breaks or permitted trailing space/tab on a closing fence.
for separator in (chr(0x2028), chr(0x2029)):
    label = f"U+{ord(separator):04X}"
    for fence in ("```", "~~~"):
        PARSE_CASES.append((f"unclosed {fence} with {label} in info",
                            fence + "lang" + separator + "info\n" + block("hidden"), none()))
        for suffix in (separator, separator + "info"):
            PARSE_CASES.append((f"{fence} close rejects suffix {suffix!r}",
                                fence + "\n" + fence + suffix + "\n" + block("hidden"), none()))
    PARSE_CASES.append((f"backtick after {label} disallows opening",
                        "```lang" + separator + "info`bad\n" + block("A"), valid("A")))

NORMALIZE_CASES = [
    ("ordinary text", "A", "A"),
    ("outer whitespace", " \t\n请审查。\n\t ", "请审查。"),
    ("internal whitespace retained", "\n A  \n\n  B\tC \n", "A  \n\n  B\tC"),
    ("CRLF to LF", " A\r\nB ", "A\nB"),
    ("CR to LF", " A\rB ", "A\nB"),
    ("mixed newlines", "A\r\nB\rC\nD", "A\nB\nC\nD"),
    ("empty string", "", ""),
    ("whitespace only", " \t\r\n\u3000", ""),
    ("no unicode normalization", "e\u0301", "e\u0301"),
]

IDENTITY = {"endpointId": "cfep_test", "sessionId": "session-1", "messageId": "assistant-1",
            "normalizedText": '请审查 "Adapter"。\n路径 \\source\t保持原样。'}
CANONICAL = json.dumps([
    "contextflow-supervisor-submission-v1", "chatgpt_web", IDENTITY["endpointId"],
    IDENTITY["sessionId"], IDENTITY["messageId"], "markdown", IDENTITY["normalizedText"],
], ensure_ascii=False, separators=(",", ":"))
EXPECTED_ID = "sub_" + hashlib.sha256(CANONICAL.encode("utf-8")).hexdigest()


class Checks:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.passed += int(ok)
        self.failed += int(not ok)
        print(("PASS " if ok else "FAIL ") + name +
              (f" :: {detail}" if detail and not ok else ""), flush=True)

    def finish(self) -> int:
        print(f"RESULT {self.passed} passed, {self.failed} failed", flush=True)
        return int(self.failed > 0)


async def run_checks(page, checks: Checks) -> None:
    readiness = await page.evaluate("() => globalThis.dispatchProtocolReady")
    available = await page.evaluate("() => typeof globalThis.ContextFlowDispatch === 'object'")
    checks.check("production ContextFlowDispatch API is available", available,
                 readiness.get("error") or "script loaded without ContextFlowDispatch")
    if not available:
        return

    contract = await page.evaluate("""() => {
        const api = globalThis.ContextFlowDispatch;
        return Object.isFrozen(api) &&
            JSON.stringify(Object.keys(api).sort()) === JSON.stringify([
                'canonicalIdentityDocument', 'normalizeFullMessage', 'parse', 'submissionIdFor'
            ]) && Object.values(api).every(value => typeof value === 'function');
    }""")
    checks.check("frozen API exports exactly four functions", contract)

    results = await page.evaluate("""texts => texts.map(text => {
        try { return ContextFlowDispatch.parse(text); }
        catch (error) { return { threw: error.name, message: error.message }; }
    })""", [case[1] for case in PARSE_CASES])
    for (name, _, expected), actual in zip(PARSE_CASES, results, strict=True):
        comparison = expected
        diagnostic_ok = True
        if expected["status"] == "invalid":
            error = actual.get("error") or {}
            diagnostic_ok = (set(error) == {"code", "message"} and
                             isinstance(error.get("message"), str) and bool(error["message"].strip()))
            comparison = {**expected, "error": {**expected["error"], "message": error.get("message")}}
        checks.check("parse: " + name, actual == comparison and diagnostic_ok,
                     f"expected {comparison!r}, got {actual!r}")

    for name, text, expected in NORMALIZE_CASES:
        actual = await page.evaluate("text => ContextFlowDispatch.normalizeFullMessage(text)", text)
        checks.check("normalize: " + name, actual == expected, f"expected {expected!r}, got {actual!r}")

    type_checks = await page.evaluate("""async () => {
        const results = [];
        const bad = [undefined, null, 42, true, [], {}, new String('text'),
                     { toString() { throw new Error('must not coerce untrusted input'); } }];
        for (const method of ['parse', 'normalizeFullMessage']) {
            for (const [index, value] of bad.entries()) {
                let ok = false;
                try { ContextFlowDispatch[method](value); }
                catch (error) { ok = error instanceof TypeError; }
                results.push({ name: `${method} rejects non-string ${index}`, ok });
            }
        }
        return results;
    }""")
    for result in type_checks:
        checks.check(result["name"], result["ok"])

    identity_checks = await page.evaluate("""async ({ input, canonical, expectedId }) => {
        const api = ContextFlowDispatch;
        const results = [];
        const check = (name, ok) => results.push({ name, ok });
        const document = api.canonicalIdentityDocument(input);
        check('canonical document is the exact JSON string',
              typeof document === 'string' && document === canonical);
        check('canonical field order ignores object insertion order',
              api.canonicalIdentityDocument({
                  normalizedText: input.normalizedText, messageId: input.messageId,
                  sessionId: input.sessionId, endpointId: input.endpointId,
              }) === canonical);
        const extra = { ...input, trigger: 'auto', user_message_id: 'user-only-evidence',
                        turn_group_id: 'turn-only-evidence', adapter: 'untrusted', format: 'html' };
        check('trigger and auxiliary fields do not enter canonical document',
              api.canonicalIdentityDocument(extra) === canonical);
        const pendingId = api.submissionIdFor(input);
        check('submissionIdFor returns a Promise', pendingId instanceof Promise);
        const id = await pendingId;
        check('SHA-256 matches independent Python UTF-8 oracle', id === expectedId);
        check('ID has sub_ and exactly 64 lowercase hex digits', /^sub_[0-9a-f]{64}$/.test(id));
        check('repeat input is stable', await api.submissionIdFor(input) === id);
        check('auto/manual trigger invariant', await api.submissionIdFor({ ...extra, trigger: 'manual' }) === id &&
              await api.submissionIdFor(extra) === id);
        for (const field of ['endpointId', 'sessionId', 'messageId', 'normalizedText']) {
            check(`changed ${field} changes ID`,
                  await api.submissionIdFor({ ...input, [field]: input[field] + 'changed' }) !== id);
        }
        check('identity document does not mutate input',
              api.canonicalIdentityDocument(Object.freeze({ ...input })) === canonical);
        check('identity values are not silently trimmed',
              JSON.parse(api.canonicalIdentityDocument({ ...input, endpointId: ' ep ' }))[2] === ' ep ');
        const normalizedLF = api.normalizeFullMessage('  A\\nB  ');
        const normalizedCRLF = api.normalizeFullMessage('  A\\r\\nB  ');
        check('normalized LF/CRLF have the same ID',
              await api.submissionIdFor({ ...input, normalizedText: normalizedLF }) ===
              await api.submissionIdFor({ ...input, normalizedText: normalizedCRLF }));
        check('parse output and manual normalization have the same ID',
              await api.submissionIdFor({ ...input,
                  normalizedText: api.parse('[CONTEXTFLOW_DISPATCH_V1]\\n A\\r\\nB \\n[/CONTEXTFLOW_DISPATCH_V1]').content }) ===
              await api.submissionIdFor({ ...input, normalizedText: normalizedLF }));
        for (const method of ['canonicalIdentityDocument', 'submissionIdFor']) {
            const badValues = [undefined, null, '', ' \\t\\n', 1, true, [], {}, new String('value')];
            for (const field of ['endpointId', 'sessionId', 'messageId', 'normalizedText']) {
                for (const [index, value] of badValues.entries()) {
                    let ok = false;
                    try { await api[method]({ ...input, [field]: value }); }
                    catch (error) { ok = error instanceof TypeError; }
                    check(`${method} rejects ${field} invalid value ${index}`, ok);
                }
            }
            for (const [index, value] of [undefined, null, [], 'input', 1, true].entries()) {
                let ok = false;
                try { await api[method](value); }
                catch (error) { ok = error instanceof TypeError; }
                check(`${method} rejects invalid document ${index}`, ok);
            }
        }
        const first = api.parse('plain');
        first.blocks.push('mutation');
        check('parse results do not share mutable block arrays', api.parse('plain').blocks.length === 0);
        return results;
    }""", {"input": IDENTITY, "canonical": CANONICAL, "expectedId": EXPECTED_ID})
    for result in identity_checks:
        checks.check("identity: " + result["name"], result["ok"])


async def main() -> int:
    checks = Checks()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as playwright:
            # Use installed Edge on Windows; Playwright Chromium on other platforms.
            browser = await playwright.chromium.launch(
                channel="msedge" if sys.platform == "win32" else "chromium",
                headless=True, args=["--disable-extensions"],
            )
            try:
                page = await browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                response = await page.goto(
                    f"http://127.0.0.1:{server.server_port}/tests/extension/dispatch_protocol_harness.html",
                    wait_until="load",
                )
                checks.check("harness is served successfully", response is not None and response.status == 200)
                await run_checks(page, checks)
                checks.check("browser has no uncaught script errors", not errors, repr(errors))
            finally:
                await browser.close()
    except Exception as error:
        checks.check("test execution", False, f"{type(error).__name__}: {error}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return checks.finish()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
