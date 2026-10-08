import hashlib
import html as html_lib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections import OrderedDict
from pathlib import Path

from backend.app.schemas.execution_result import ExecutionRequest, ExecutionResult
from backend.app.services.assessment.language_runner import register_runner

# ============================================================
# JOBPILOT AI - WEB PAGE RUNNER (HTML / CSS / JavaScript)
#
# The candidate submits ONE HTML document (with inline <style> and <script>). It is
# loaded in a REAL headless Chrome/Edge, so layout, computed CSS and JavaScript
# behaviour are checked for real, not guessed from the text.
#
# A test case's `stdin` is JSON:  {"case": "tc1", "suite": [{"id": "tc1", "steps": [...]}, ...]}
# The whole suite of a question runs in ONE browser launch (cached per submission),
# and this runner returns that case's line: "PASS" or "FAIL: <reason>".
#
# Steps (a tiny declarative language; hidden tests are never free-form JavaScript):
#   {"do": "click", "sel": "#add"}                 {"do": "input", "sel": "#name", "value": "Asha"}
#   {"do": "wait", "ms": 100}
#   {"expect": "exists", "sel": "nav a", "min": 3} {"expect": "count", "sel": "li", "n": 4}
#   {"expect": "text", "sel": "h1", "equals": "Hi"|"contains": "Hi", "index": 0}
#   {"expect": "texts", "sel": "li", "equals": ["a", "b"]}
#   {"expect": "attr", "sel": "img", "name": "alt", "nonempty": true | "equals": "x" | "contains": "x"}
#   {"expect": "style", "sel": ".card", "prop": "display", "equals": "flex" | "oneof": ["a", "b"]}
#   {"expect": "visible", "sel": "#panel", "value": true}   {"expect": "tag", "sel": "#hero", "is": "section"}
#   {"expect": "value", "sel": "#name", "equals": ""}
#
# Safety: the page runs with a Content-Security-Policy that blocks every network request,
# in a throw-away browser profile, killed after a hard time limit. The check code is injected
# BEFORE the candidate's scripts and keeps private references to the browser's own functions,
# so candidate code cannot fake a result; the result is read back under a random per-run key.
# ============================================================

RUNNER_VERSION = "html-headless-browser-1.0"

RUN_TIMEOUT_SECONDS = 25
VIRTUAL_TIME_BUDGET_MS = 9000
CACHE_SIZE = 64

CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:"

_BROWSER_CANDIDATES = [
    os.getenv("CHROME_PATH") or "",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]

_HARNESS = r"""
(function () {
  var NONCE = "__NONCE__";
  var SUITE = __SUITE__;
  var qs = Document.prototype.querySelector, qsa = Document.prototype.querySelectorAll;
  var gcs = window.getComputedStyle.bind(window);
  var getAttr = Element.prototype.getAttribute, setAttr = Element.prototype.setAttribute;
  var clickFn = HTMLElement.prototype.click, dispatch = EventTarget.prototype.dispatchEvent;
  var addL = EventTarget.prototype.addEventListener, later = window.setTimeout.bind(window);
  var stringify = JSON.stringify.bind(JSON), EventC = window.Event, doc = document;
  var textGet = Object.getOwnPropertyDescriptor(Node.prototype, "textContent").get;
  var valueSetters = {
    INPUT: Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set,
    TEXTAREA: Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set,
    SELECT: Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set
  };
  var valueGetters = {
    INPUT: Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").get,
    TEXTAREA: Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").get,
    SELECT: Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").get
  };
  function all(sel) { return Array.prototype.slice.call(qsa.call(doc, sel)); }
  function one(sel, index) {
    var list = all(sel), el = list[index || 0];
    if (!el) { throw new Error("no element matches '" + sel + "'"); }
    return el;
  }
  function text(el) { return (textGet.call(el) || "").replace(/\s+/g, " ").trim(); }
  function norm(v) { return String(v).toLowerCase().replace(/\s+/g, " ").trim(); }
  function visible(el) {
    var s = gcs(el), r = el.getBoundingClientRect();
    return s.display !== "none" && s.visibility !== "hidden" && r.width > 0 && r.height > 0;
  }
  function pause(ms) { return new Promise(function (resolve) { later(resolve, ms); }); }
  async function step(s) {
    if (s["do"] === "click") { clickFn.call(one(s.sel, s.index)); await pause(30); return; }
    if (s["do"] === "wait") { await pause(s.ms || 50); return; }
    if (s["do"] === "input") {
      var el = one(s.sel, s.index), set = valueSetters[el.tagName];
      if (!set) { throw new Error("'" + s.sel + "' is not a form field"); }
      set.call(el, s.value);
      dispatch.call(el, new EventC("input", { bubbles: true }));
      dispatch.call(el, new EventC("change", { bubbles: true }));
      await pause(30); return;
    }
    var kind = s.expect, list, el, actual;
    if (kind === "exists") {
      list = all(s.sel);
      if (list.length < (s.min || 1)) { throw new Error("expected at least " + (s.min || 1) + " element(s) matching '" + s.sel + "', found " + list.length); }
    } else if (kind === "count") {
      list = all(s.sel);
      if (list.length !== s.n) { throw new Error("expected " + s.n + " element(s) matching '" + s.sel + "', found " + list.length); }
    } else if (kind === "text") {
      actual = text(one(s.sel, s.index));
      if (s.equals !== undefined && norm(actual) !== norm(s.equals)) { throw new Error("'" + s.sel + "' shows '" + actual + "', expected '" + s.equals + "'"); }
      if (s.contains !== undefined && norm(actual).indexOf(norm(s.contains)) < 0) { throw new Error("'" + s.sel + "' shows '" + actual + "', expected it to contain '" + s.contains + "'"); }
    } else if (kind === "texts") {
      actual = all(s.sel).map(text);
      if (stringify(actual.map(norm)) !== stringify(s.equals.map(norm))) { throw new Error("'" + s.sel + "' items are " + stringify(actual) + ", expected " + stringify(s.equals)); }
    } else if (kind === "attr") {
      actual = getAttr.call(one(s.sel, s.index), s.name);
      if (s.nonempty && (actual === null || String(actual).trim() === "")) { throw new Error("'" + s.sel + "' needs a non-empty " + s.name + " attribute"); }
      if (s.equals !== undefined && norm(actual === null ? "" : actual) !== norm(s.equals)) { throw new Error("'" + s.sel + "' " + s.name + " is '" + actual + "', expected '" + s.equals + "'"); }
      if (s.contains !== undefined && norm(actual === null ? "" : actual).indexOf(norm(s.contains)) < 0) { throw new Error("'" + s.sel + "' " + s.name + " should contain '" + s.contains + "'"); }
    } else if (kind === "style") {
      actual = norm(gcs(one(s.sel, s.index)).getPropertyValue(s.prop));
      var options = (s.oneof || [s.equals]).map(norm);
      if (options.indexOf(actual) < 0) { throw new Error("'" + s.sel + "' has " + s.prop + " '" + actual + "', expected " + options.join(" or ")); }
    } else if (kind === "visible") {
      if (visible(one(s.sel, s.index)) !== (s.value !== false)) { throw new Error("'" + s.sel + "' should " + (s.value !== false ? "be visible" : "be hidden")); }
    } else if (kind === "tag") {
      actual = one(s.sel, s.index).tagName.toLowerCase();
      if (actual !== String(s.is).toLowerCase()) { throw new Error("'" + s.sel + "' is a <" + actual + ">, expected <" + s.is + ">"); }
    } else if (kind === "value") {
      el = one(s.sel, s.index);
      actual = valueGetters[el.tagName] ? valueGetters[el.tagName].call(el) : "";
      if (norm(actual) !== norm(s.equals)) { throw new Error("'" + s.sel + "' value is '" + actual + "', expected '" + s.equals + "'"); }
    } else {
      throw new Error("unknown check");
    }
  }
  async function main() {
    var results = {};
    for (var i = 0; i < SUITE.length; i++) {
      var c = SUITE[i];
      try { for (var j = 0; j < c.steps.length; j++) { await step(c.steps[j]); } results[c.id] = "PASS"; }
      catch (e) { results[c.id] = "FAIL: " + ((e && e.message) || String(e)); }
    }
    setAttr.call(doc.documentElement, "data-jp-" + NONCE, stringify(results));
  }
  addL.call(window, "load", function () { later(main, 60); });
})();
"""

_cache: "OrderedDict[str, dict]" = OrderedDict()


def find_browser() -> str | None:

    for candidate in _BROWSER_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate

    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found

    return None


def _clean_environment() -> dict:

    env = {}

    for key, value in os.environ.items():
        upper = key.upper()
        if any(marker in upper for marker in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE")):
            continue                                                    # the browser never sees the platform's secrets
        env[key] = value

    return env


def _inject(source: str, nonce: str, suite: list) -> str:

    suite_json = json.dumps(suite).replace("</", "<\\/")
    harness = _HARNESS.replace("__NONCE__", nonce).replace("__SUITE__", suite_json)
    head_bits = f'<meta http-equiv="Content-Security-Policy" content="{CSP}"><script>{harness}</script>'

    match = re.search(r"<head[^>]*>", source, re.I)

    if match:
        return source[:match.end()] + head_bits + source[match.end():]

    match = re.search(r"<html[^>]*>", source, re.I)

    if match:
        return source[:match.end()] + "<head>" + head_bits + "</head>" + source[match.end():]

    return "<!DOCTYPE html><html><head>" + head_bits + "</head><body>" + source + "</body></html>"


class HtmlRunError(Exception):

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def run_suite(source: str, suite: list) -> dict:
    """Runs every case of `suite` against the page in one browser launch; {case_id: 'PASS' | 'FAIL: reason'}."""

    key = hashlib.sha256((source + "\u0000" + json.dumps(suite, sort_keys=True)).encode("utf-8")).hexdigest()

    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    browser = find_browser()

    if browser is None:
        raise HtmlRunError("runner_unavailable", "No headless browser (Chrome or Edge) is available on the server.")

    nonce = secrets.token_hex(12)
    workdir = tempfile.mkdtemp(prefix="jp_html_")

    try:

        page = Path(workdir) / "page.html"
        page.write_text(_inject(source, nonce, suite), encoding="utf-8")

        command = [
            browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-background-networking", "--disable-sync", "--mute-audio",
            f"--user-data-dir={Path(workdir) / 'profile'}", "--window-size=1280,900",
            f"--virtual-time-budget={VIRTUAL_TIME_BUDGET_MS}", "--dump-dom", page.as_uri(),
        ]

        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=RUN_TIMEOUT_SECONDS, env=_clean_environment(),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise HtmlRunError("timeout", "The page did not finish loading (check for an endless loop in your script).") from exc

        output = completed.stdout.decode("utf-8", errors="replace")
        match = re.search(r'data-jp-' + nonce + r'="([^"]*)"', output)

        if not match:
            raise HtmlRunError(
                "runtime_error",
                "The page did not finish running its checks (a script error or endless loop can cause this).",
            )

        results = json.loads(html_lib.unescape(match.group(1)))

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    _cache[key] = results

    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)

    return results


class HtmlRunner:

    language = "html"
    runner_version = RUNNER_VERSION

    def available(self) -> bool:
        return find_browser() is not None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:

        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        try:

            spec = json.loads(request.stdin)
            case_id, suite = spec["case"], spec["suite"]
            results = run_suite(request.source_code, suite)

        except HtmlRunError as exc:

            return ExecutionResult(
                success=False, stderr=exc.message, exit_code=1, timed_out=exc.error_type == "timeout",
                execution_time_ms=elapsed(), error_type=exc.error_type, message=exc.message,
                language="html", runner_version=RUNNER_VERSION,
            )

        except Exception:

            return ExecutionResult(
                success=False, exit_code=1, execution_time_ms=elapsed(), error_type="sandbox_error",
                message="The web page sandbox could not run this test.", language="html", runner_version=RUNNER_VERSION,
            )

        return ExecutionResult(
            success=True, stdout=results.get(case_id, "FAIL: this check did not run"), exit_code=0,
            execution_time_ms=elapsed(), error_type="success", language="html", runner_version=RUNNER_VERSION,
        )


register_runner(HtmlRunner())
