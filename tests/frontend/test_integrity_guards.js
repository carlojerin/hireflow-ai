// Node: pure helpers + install() of integrity-guards.js with a fake document/window.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const mod = { exports: {} };
new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "integrity-guards.js"), "utf8"))(mod, mod.exports);
const G = mod.exports;

const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

test("shortcut names: developer tools / view source / save / print are recognised, normal typing is not", () => {
    assert.strictEqual(G.shortcutName({ key: "F12" }), "F12");
    assert.strictEqual(G.shortcutName({ key: "i", ctrlKey: true, shiftKey: true }), "Ctrl+Shift+I");
    assert.strictEqual(G.shortcutName({ key: "J", metaKey: true, shiftKey: true }), "Ctrl+Shift+J");
    assert.strictEqual(G.shortcutName({ key: "u", ctrlKey: true }), "Ctrl+U");
    assert.strictEqual(G.shortcutName({ key: "s", ctrlKey: true }), "Ctrl+S");
    assert.strictEqual(G.shortcutName({ key: "p", ctrlKey: true }), "Ctrl+P");
    assert.strictEqual(G.shortcutName({ key: "PrintScreen" }), "PrintScreen");
    assert.strictEqual(G.shortcutName({ key: "c", ctrlKey: true }), null);          // plain copy is handled by the clipboard guard
    assert.strictEqual(G.shortcutName({ key: "a" }), null);
    assert.strictEqual(G.shortcutName({ key: "Enter", ctrlKey: true }), null);      // run code
});

test("developer tools heuristic: docked panel yes, ordinary browser chrome no", () => {
    assert.strictEqual(G.devtoolsLikelyOpen({ outerWidth: 1920, innerWidth: 1905, outerHeight: 1040, innerHeight: 910 }), false);
    assert.strictEqual(G.devtoolsLikelyOpen({ outerWidth: 1920, innerWidth: 1300, outerHeight: 1040, innerHeight: 910 }), true);
    assert.strictEqual(G.devtoolsLikelyOpen({ outerWidth: 1920, innerWidth: 1905, outerHeight: 1040, innerHeight: 600 }), true);
});

test("clipboard target: editor vs answer box vs page", () => {
    const el = (tag, inEditor) => ({ tagName: tag, isContentEditable: false, closest: (sel) => (inEditor && /monaco-editor|pv3EditorHost/.test(sel) ? {} : null) });
    assert.strictEqual(G.clipboardTarget(el("TEXTAREA", true)), "editor");
    assert.strictEqual(G.clipboardTarget(el("TEXTAREA", false)), "answer_box");
    assert.strictEqual(G.clipboardTarget(el("DIV", false)), "page");
});

function fakeEnv(extra) {
    const listeners = {};
    const doc = { addEventListener: (t, h) => { (listeners[t] = listeners[t] || []).push(h); }, removeEventListener: () => {} };
    const win = Object.assign({ outerWidth: 1920, innerWidth: 1905, outerHeight: 1040, innerHeight: 910, screen: {}, setInterval: () => 1, clearInterval: () => {}, getSelection: () => ({ toString: () => "selected question text" }) }, extra || {});
    return { doc, win, listeners };
}
function ev(props) { return Object.assign({ prevented: false, preventDefault() { this.prevented = true; }, target: { tagName: "DIV", closest: () => null } }, props); }

test("paste outside the editor is blocked and reported with its size only", () => {
    const { doc, win, listeners } = fakeEnv();
    const seen = [];
    G.install((t, m) => seen.push([t, m]), doc, win);
    const e = ev({ clipboardData: { getData: () => "x".repeat(250) }, target: { tagName: "TEXTAREA", closest: () => null } });
    listeners.paste[0](e);
    assert.ok(e.prevented);
    assert.deepStrictEqual(seen[0], ["paste_detected", { chars: 250, target: "answer_box" }]);
});

test("paste inside the code editor is allowed but still reported", () => {
    const { doc, win, listeners } = fakeEnv();
    const seen = [];
    G.install((t, m) => seen.push([t, m]), doc, win);
    const e = ev({ clipboardData: { getData: () => "y".repeat(500) }, target: { tagName: "TEXTAREA", closest: () => ({}) } });
    listeners.paste[0](e);
    assert.ok(!e.prevented);
    assert.deepStrictEqual(seen[0], ["paste_detected", { chars: 500, target: "editor" }]);
});

test("copy of page text is blocked; right-click and F12 are blocked and reported", () => {
    const { doc, win, listeners } = fakeEnv();
    const seen = [];
    G.install((t, m) => seen.push([t, m]), doc, win);
    const copy = ev({}); listeners.copy[0](copy);
    const menu = ev({}); listeners.contextmenu[0](menu);
    const f12 = ev({ key: "F12" }); listeners.keydown.forEach((l) => l(f12));
    assert.ok(copy.prevented && menu.prevented && f12.prevented);
    assert.deepStrictEqual(seen.map((s) => s[0]), ["copy_detected", "blocked_shortcut", "blocked_shortcut"]);
    assert.deepStrictEqual(seen[2][1], { key: "F12" });
});

test("an extended (second) screen is reported once at install", () => {
    const { doc, win } = fakeEnv({ screen: { isExtended: true } });
    const seen = [];
    G.install((t) => seen.push(t), doc, win);
    assert.deepStrictEqual(seen, ["multiple_monitors_detected"]);
});

results.forEach(([n, s]) => process.stdout.write(`${s.padEnd(5)}  ${n}\n`));
const failed = results.filter(([, s]) => s === "FAIL");
results.forEach(([n, s, d]) => { if (s === "FAIL") process.stdout.write(`\n--- FAIL: ${n} ---\n${d}\n`); });
process.stdout.write("=".repeat(70) + `\n${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length ? 1 : 0);
