// The detector watches a TABLE of objects (phone, laptop, remote, book), each with its own class and threshold.
//   node tests/frontend/test_object_table.js
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

const ANCHORS = 8400;
const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

// a synthetic YOLOv8 output [1,84,8400]: put one box of the given COCO class at anchor `anchor`
function output(placements) {
    const data = new Float32Array(84 * ANCHORS);
    placements.forEach(({ anchor, classIndex, score, cx = 320, cy = 320, w = 80, h = 120 }) => {
        data[0 * ANCHORS + anchor] = cx; data[1 * ANCHORS + anchor] = cy; data[2 * ANCHORS + anchor] = w; data[3 * ANCHORS + anchor] = h;
        data[(4 + classIndex) * ANCHORS + anchor] = score;
    });
    return data;
}

test("the table lists a phone, a laptop, a remote and a book, and not the candidate's own tv / keyboard / mouse", () => {
    const names = PD.objectTable.map((e) => e.name);
    assert.deepStrictEqual(names, ["cell phone", "laptop", "remote", "book"]);
    ["tv", "keyboard", "mouse"].forEach((n) => assert.ok(!names.includes(n), n));
});

test("every row maps to the right COCO class index", () => {
    const index = Object.fromEntries(PD.objectTable.map((e) => [e.name, e.classIndex]));
    assert.deepStrictEqual(index, { "cell phone": 67, laptop: 63, remote: 65, book: 73 });
});

test("a phone, a laptop and a book in one frame come back as three detections with their own names", () => {
    const found = PD.decodeDetections(output([
        { anchor: 10, classIndex: 67, score: 0.8, cx: 100 },
        { anchor: 20, classIndex: 63, score: 0.9, cx: 300 },
        { anchor: 30, classIndex: 73, score: 0.8, cx: 500 },
    ]), 1, 0, 0);
    assert.deepStrictEqual(found.map((d) => d.object).sort(), ["book", "cell phone", "laptop"]);
});

test("a weak laptop score is ignored (higher threshold than a phone) while the same weak score on a phone counts", () => {
    const weak = output([{ anchor: 5, classIndex: 63, score: 0.35 }, { anchor: 6, classIndex: 67, score: 0.35, cx: 500 }]);
    const found = PD.decodeDetections(weak, 1, 0, 0);
    assert.deepStrictEqual(found.map((d) => d.object), ["cell phone"]);
});

test("an empty frame finds nothing", () => {
    assert.strictEqual(PD.decodeDetections(output([]), 1, 0, 0).length, 0);
});

const failed = results.filter((r) => r[1] === "FAIL");
results.forEach(([name, status, detail]) => console.log(`${status}  ${name}${detail ? "\n" + detail : ""}`));
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
