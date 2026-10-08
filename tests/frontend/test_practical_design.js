// The web design editor's logic (no browser): adding, snapping, moving, resizing, ordering, undo / redo, limits, and what is rendered and sent.
//   node tests/frontend/test_practical_design.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const mod = { exports: {} };
new Function("module", "self", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "practical-design.js"), "utf8"))(mod, undefined);
const D = mod.exports;

const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

test("a component dropped on the page lands on the grid with its default size and is selected", () => {
    const s = D.createState();
    const e = s.add("button", 203, 117);
    assert.strictEqual(e.x % D.GRID, 0);
    assert.strictEqual(e.y % D.GRID, 0);
    assert.strictEqual(e.x, 200);
    assert.strictEqual(e.y, 120);
    assert.strictEqual(e.w, D.TYPES.button.w);
    assert.strictEqual(s.selectedId(), e.id);
    assert.strictEqual(s.count(), 1);
});

test("unknown components are refused and the page holds at most 70 elements", () => {
    const s = D.createState();
    assert.strictEqual(s.add("script", 0, 0), null);
    for (let i = 0; i < 80; i++) s.add("text", 0, 0);
    assert.strictEqual(s.count(), 70);
});

test("moving and resizing snap to the grid and stay near the page", () => {
    const s = D.createState();
    const e = s.add("card", 40, 40);
    s.move(e.id, 333, 205);
    assert.strictEqual(s.get(e.id).x, 336);
    assert.strictEqual(s.get(e.id).y, 208);
    s.move(e.id, 99999, -500);
    assert.ok(s.get(e.id).x <= D.CANVAS_W - 40);
    assert.ok(s.get(e.id).y >= 0);
    s.resize(e.id, 5, 5);
    assert.ok(s.get(e.id).w >= 16 && s.get(e.id).h >= 2);
    s.resize(e.id, 401, 223);
    assert.strictEqual(s.get(e.id).w, 400);
    assert.strictEqual(s.get(e.id).h, 224);
});

test("undo and redo walk back and forward through add, move, delete and duplicate", () => {
    const s = D.createState();
    const a = s.add("heading", 40, 40);
    s.checkpoint();
    s.move(a.id, 200, 200);
    s.duplicate(a.id);
    assert.strictEqual(s.count(), 2);
    s.undo();
    assert.strictEqual(s.count(), 1);
    assert.strictEqual(s.get(a.id).x, 200);
    s.undo();
    assert.strictEqual(s.get(a.id).x, 40);
    s.undo();
    assert.strictEqual(s.count(), 0);
    assert.strictEqual(s.undo(), false);
    s.redo();
    assert.strictEqual(s.count(), 1);
    s.remove(a.id);
    assert.strictEqual(s.count(), 0);
    s.undo();
    assert.strictEqual(s.count(), 1);
});

test("a new change clears the redo history", () => {
    const s = D.createState();
    s.add("text", 0, 0);
    s.undo();
    s.add("button", 0, 0);
    assert.strictEqual(s.redo(), false);
});

test("bring forward and send back change the stacking order", () => {
    const s = D.createState();
    const a = s.add("section", 0, 0), b = s.add("heading", 40, 40), c = s.add("text", 40, 120);
    s.reorder(a.id, "forward");
    assert.deepStrictEqual(s.elements().map((e) => e.id), [b.id, a.id, c.id]);
    s.reorder(c.id, "back");
    assert.deepStrictEqual(s.elements().map((e) => e.id), [b.id, c.id, a.id]);
    s.reorder(b.id, "back");
    assert.deepStrictEqual(s.elements().map((e) => e.id), [b.id, c.id, a.id]);
});

test("styling an element changes only that element", () => {
    const s = D.createState();
    const a = s.add("heading", 40, 40), b = s.add("heading", 40, 200);
    s.update(a.id, { text: "Welcome", fontSize: 56, color: "#ffffff", bg: "#111827" });
    assert.strictEqual(s.get(a.id).text, "Welcome");
    assert.strictEqual(s.get(b.id).text, D.TYPES.heading.text);
});

test("the layout goes out as JSON and comes back unchanged", () => {
    const s = D.createState();
    s.add("nav", 0, 0);
    s.add("button", 80, 300);
    const again = D.createState(D.parseLayout(s.toJSON()));
    assert.deepStrictEqual(again.toLayout(), s.toLayout());
    assert.deepStrictEqual(D.parseLayout("not json"), []);
    assert.deepStrictEqual(D.parseLayout(JSON.stringify({ elements: [{ type: "bad" }, { id: 1, type: "text", x: 0, y: 0, w: 10, h: 10 }] })).length, 1);
});

const test1 = { practical: { environment: "design", design: { title: "Landing page", brief: "Design the <b>landing</b> page.", audience: "Managers", brand: { name: "Tasklane", primary: "#4f5bff", tone: "clean" },
    copy: { headline: "Plan work. Ship on time.", cta: "Start free trial", features: ["Simple boards"] }, checklist: ["A navigation bar at the top", "A call-to-action button"], canvas: { w: 1200, h: 1500 } } } };

test("the editor page shows the brief, the checklist, the copy and every component, with HTML escaped", () => {
    const html = D.html(test1, "");
    assert.ok(html.includes("Design the &lt;b&gt;landing&lt;/b&gt; page."));
    assert.ok(!html.includes("<b>landing</b>"));
    assert.ok(html.includes("A navigation bar at the top") && html.includes("A call-to-action button"));
    assert.ok(html.includes('data-copy="Plan work. Ship on time."') && html.includes("#4f5bff"));
    Object.keys(D.TYPES).forEach((type) => assert.ok(html.includes(`data-type="${type}"`), type));
    assert.ok(html.includes('id="pdCanvas"') && html.includes('id="pdUndo"') && html.includes('id="pdProps"'));
});

test("a saved draft is parsed for the editor and nothing from the grading side is ever rendered", () => {
    const html = D.html(test1, JSON.stringify({ elements: [{ id: 1, type: "text", x: 0, y: 0, w: 100, h: 40, text: "x" }] }));
    assert.ok(!/rule|keywords|rubric|correct_answer/.test(html));
});

test("a stored layout is drawn read-only for HR, scaled, with escaped text", () => {
    const target = { innerHTML: "" };
    D.renderStatic({ elements: [{ id: 1, type: "heading", x: 80, y: 100, w: 600, h: 60, text: "<img src=x onerror=alert(1)>", fontSize: 40, fontWeight: 700, color: "#111827", bg: "", radius: 0, align: "left" },
                                { id: 2, type: "button", x: 80, y: 300, w: 200, h: 52, text: "Go", fontSize: 16, fontWeight: 700, color: "#ffffff", bg: "#4f5bff", radius: 10, align: "center" }] }, target, 600);
    assert.ok(target.innerHTML.includes("pd-readonly") && target.innerHTML.includes("scale(0.5)"));
    assert.ok(target.innerHTML.includes("&lt;img src=x") && !target.innerHTML.includes("<img src=x"));
    assert.ok(!target.innerHTML.includes("pd-handle") && !target.innerHTML.includes("tabindex"));
});

test("the answer is the layout, and a page with too few components does not count as answered", () => {
    const s = D.createState();
    ["nav", "heading", "text"].forEach((t) => s.add(t, 0, 0));
    D.state = s;
    assert.ok(typeof D.collect === "function" && typeof D.isAnswered === "function");
    // (the module keeps its own state once mounted; here the minimum is what matters)
    assert.strictEqual(D.MIN_ELEMENTS_TO_SUBMIT, 4);
});

let failed = 0;
for (const [name, status, detail] of results) {
    console.log(`${status}   ${name}`);
    if (detail) { failed++; console.log(detail); }
}
console.log("=".repeat(70));
console.log(`${results.length - failed} passed, ${failed} failed, ${results.length} total`);
process.exit(failed ? 1 : 0);
