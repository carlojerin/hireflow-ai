const assert = require("assert");
const fs = require("fs");
const path = require("path");
const mod = { exports: {} };
new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "exam-lock.js"), "utf8"))(mod, mod.exports);
const { lockReasons } = mod.exports;
const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }
const ok = { fullscreenRequired: true, isFullscreen: true, hidden: false, hasFocus: true };

test("everything in order: the exam is visible", () => assert.deepStrictEqual(lockReasons(ok), []));
test("leaving full screen locks", () => assert.deepStrictEqual(lockReasons({ ...ok, isFullscreen: false }), ["fullscreen"]));
test("another tab locks", () => assert.deepStrictEqual(lockReasons({ ...ok, hidden: true }), ["tab"]));
test("another window / application (focus lost, still fullscreen) locks", () => assert.deepStrictEqual(lockReasons({ ...ok, hasFocus: false }), ["window"]));
test("a browser without full screen support is not locked for that reason", () => assert.deepStrictEqual(lockReasons({ ...ok, fullscreenRequired: false, isFullscreen: false }), []));
test("several reasons are all reported, the first is shown", () => assert.deepStrictEqual(lockReasons({ fullscreenRequired: true, isFullscreen: false, hidden: true, hasFocus: false }), ["fullscreen", "tab", "window"]));


// ---- behaviour with a minimal fake DOM (no browser needed) ----
function fakeEnv() {
    const listeners = { doc: {}, win: {} };
    const makeEl = () => {
        const attrs = {};
        const el = { style: {}, children: [], _attrs: attrs,
            setAttribute(k, v) { attrs[k] = v; }, removeAttribute(k) { delete attrs[k]; }, getAttribute(k) { return attrs[k]; }, hasAttribute(k) { return k in attrs; },
            appendChild(c) { this.children.push(c); }, remove() { doc.body.children.splice(doc.body.children.indexOf(el), 1); },
            _q: {}, querySelector(sel) { return this._q[sel] || (this._q[sel] = { textContent: "", onclick: null }); } };
        Object.defineProperty(el, "innerHTML", { set() {}, get() { return ""; } });
        return el;
    };
    const app = makeEl();
    const doc = {
        fullscreenEnabled: true, fullscreenElement: null, visibilityState: "visible", _focus: true,
        body: { children: [app], appendChild(c) { this.children.push(c); } },
        createElement: makeEl, hasFocus() { return this._focus; },
        documentElement: { requestFullscreen() { doc.fullscreenElement = {}; return Promise.resolve(); } },
        addEventListener(t, h) { (listeners.doc[t] = listeners.doc[t] || []).push(h); }, removeEventListener() {},
    };
    const win = { navigator: { keyboard: { lock() { win._locked = true; return Promise.resolve(); }, unlock() {} } }, location: { href: "x" },
        history: { pushState() {} }, setTimeout, addEventListener(t, h) { (listeners.win[t] = listeners.win[t] || []).push(h); }, removeEventListener() {} };
    const fire = (where, type) => (listeners[where][type] || []).forEach((h) => h({}));
    return { doc, win, app, fire };
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const asyncTests = [];
function atest(name, fn) { asyncTests.push([name, fn]); }

atest("exam is hidden until the candidate is in full screen, and one click on resume opens it", async () => {
    const { doc, win, app, fire } = fakeEnv();
    const lock = new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "exam-lock.js"), "utf8"));
    const m = { exports: {} }; lock(m, m.exports); const L = m.exports;
    L.start({ document: doc, window: win });
    assert.ok(L.isLocked());
    assert.strictEqual(app.style.visibility, "hidden");
    assert.ok(app.hasAttribute("inert"));
    const overlay = doc.body.children.find((c) => c.style && c.style.display !== undefined && c !== app);
    overlay.querySelector("#examLockResume").onclick();
    await wait(400);
    assert.ok(!L.isLocked());
    assert.strictEqual(app.style.visibility, "");
    assert.ok(!app.hasAttribute("inert"));
    assert.ok(win._locked, "Escape key was captured (Keyboard Lock)");
});

atest("leaving the window or the tab locks at once; coming back in full screen unlocks; leaving full screen needs a click", async () => {
    const { doc, win, app, fire } = fakeEnv();
    const m = { exports: {} }; new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "exam-lock.js"), "utf8"))(m, m.exports); const L = m.exports;
    doc.fullscreenElement = {};                                           // the candidate started in full screen
    L.start({ document: doc, window: win });
    assert.ok(!L.isLocked());
    doc._focus = false; fire("win", "blur"); await wait(120);            // alt-tab to another application
    assert.ok(L.isLocked() && app.style.visibility === "hidden");
    doc._focus = true; fire("win", "focus"); await wait(120);
    assert.ok(!L.isLocked());
    doc.visibilityState = "hidden"; fire("doc", "visibilitychange");     // another tab
    assert.ok(L.isLocked());
    doc.visibilityState = "visible"; fire("doc", "visibilitychange");
    assert.ok(!L.isLocked());
    doc.fullscreenElement = null; fire("doc", "fullscreenchange"); await wait(120);   // Esc / F11
    assert.ok(L.isLocked());
    L.setWarning(2, 3);
    const overlay = doc.body.children.find((c) => c !== app);
    assert.strictEqual(overlay.querySelector("#examLockWarning").textContent, "Violation 2 recorded. Repeated violations end the assessment.");
    overlay.querySelector("#examLockResume").onclick(); await wait(400);
    assert.ok(!L.isLocked());
    L.stop();
    assert.strictEqual(doc.body.children.length, 1);                      // the lock screen is gone
});

(async () => {
    for (const [name, fn] of asyncTests) {
        try { await fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); }
    }
    finish();
})();

function finish() {
results.forEach(([n, s]) => process.stdout.write(`${s.padEnd(5)}  ${n}\n`));
const failed = results.filter(([, s]) => s === "FAIL");
results.forEach(([n, s, d]) => { if (s === "FAIL") process.stdout.write(`\n--- FAIL: ${n} ---\n${d}\n`); });
process.stdout.write("=".repeat(70) + `\n${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length ? 1 : 0);
}
