// A phone held up to the camera must be caught in about a second, not after many slow cycles.
//   node tests/frontend/test_phone_fast_detection.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const sandboxWindow = {};
const source = fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "phone-detector.js"), "utf8");
new Function("window", "document", "navigator", "performance", source)(
    sandboxWindow,
    { hidden: false, createElement: () => ({}) },
    { gpu: undefined },
    { now: () => Date.now() },
);
const PD = sandboxWindow.PhoneDetector;

const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

const box = [100, 100, 80, 140];
const detection = (score) => ({ score, bbox: box });

test("a clear phone (score 0.6) is confirmed on the very first frame", () => {
    const tracker = PD.createTracker();
    const result = tracker.update([detection(0.6)], 1000);
    assert.strictEqual(result.newlyConfirmed.length, 1);
});

test("a weaker sighting needs two cycles, but only about half a second", () => {
    const tracker = PD.createTracker();
    assert.strictEqual(tracker.update([detection(0.4)], 1000).newlyConfirmed.length, 0);
    assert.strictEqual(tracker.update([detection(0.4)], 1250).newlyConfirmed.length, 0);      // 250 ms: not enough persistence yet
    assert.strictEqual(tracker.update([detection(0.4)], 1500).newlyConfirmed.length, 1);      // 500 ms and 3 sightings
});

test("a single weak flicker is still ignored", () => {
    const tracker = PD.createTracker();
    assert.strictEqual(tracker.update([detection(0.35)], 1000).newlyConfirmed.length, 0);
    assert.strictEqual(tracker.update([], 1250).newlyConfirmed.length, 0);
    assert.strictEqual(tracker.update([], 1500).newlyConfirmed.length, 0);
});

test("a phone is reported once per episode, not on every frame", () => {
    const tracker = PD.createTracker();
    let confirmed = 0;
    for (let t = 1000; t < 4000; t += 250) confirmed += tracker.update([detection(0.7)], t).newlyConfirmed.length;
    assert.strictEqual(confirmed, 1);
});

test("the camera is sampled every 250 ms (it was 800 ms)", () => {
    assert.ok(/PHONE_DETECTION_INTERVAL_MS = 250/.test(source));
});

let failed = 0;
for (const [name, status, detail] of results) {
    console.log(`${status}   ${name}`);
    if (detail) { failed++; console.log(detail); }
}
console.log("=".repeat(70));
console.log(`${results.length - failed} passed, ${failed} failed, ${results.length} total`);
process.exit(failed ? 1 : 0);
