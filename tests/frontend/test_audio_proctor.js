const assert = require("assert");
const fs = require("fs");
const path = require("path");
const mod = { exports: {} };
new Function("module", "exports", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "audio-proctor.js"), "utf8"))(mod, mod.exports);
const { createEngine } = mod.exports;
const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

function run(engine, rms, ms, from) {
    const flags = [];
    for (let t = from; t < from + ms; t += 100) if (engine.step(rms, 100, t)) flags.push(t);
    return { flags, end: from + ms };
}

test("a quiet room never raises a flag", () => {
    const e = createEngine();
    const r = run(e, 0.004, 120000, 0);
    assert.deepStrictEqual(r.flags, []);
});

test("sustained talking raises exactly one flag per cooldown", () => {
    const e = createEngine();
    let r = run(e, 0.004, 4000, 0);                  // calibration in a quiet room
    r = run(e, 0.09, 8000, r.end);
    assert.strictEqual(r.flags.length, 1);
    r = run(e, 0.09, 20000, r.end);                  // still talking inside the cooldown
    assert.strictEqual(r.flags.length, 0);
});

test("a cough or a short word does not count", () => {
    const e = createEngine();
    let r = run(e, 0.004, 4000, 0);
    r = run(e, 0.12, 800, r.end);
    r = run(e, 0.004, 20000, r.end);
    assert.deepStrictEqual(r.flags, []);
});

test("a noisy room (fan, street) needs speech clearly above its own floor", () => {
    const e = createEngine();
    let r = run(e, 0.03, 4000, 0);                   // constant 0.03 background
    r = run(e, 0.03, 30000, r.end);
    assert.deepStrictEqual(r.flags, []);
    r = run(e, 0.2, 6000, r.end);
    assert.ok(r.flags.length >= 1);
});

results.forEach(([n, s]) => process.stdout.write(`${s.padEnd(5)}  ${n}\n`));
const failed = results.filter(([, s]) => s === "FAIL");
results.forEach(([n, s, d]) => { if (s === "FAIL") process.stdout.write(`\n--- FAIL: ${n} ---\n${d}\n`); });
process.stdout.write("=".repeat(70) + `\n${results.length - failed.length} passed, ${failed.length} failed, ${results.length} total\n`);
process.exit(failed.length ? 1 : 0);
