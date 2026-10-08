// The practical-test environments in the candidate's browser: pure helpers and rendering (no server, no answer key).
//   node tests/frontend/test_practical_environment.js
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const mod = { exports: {} };
new Function("module", "self", fs.readFileSync(path.join(__dirname, "..", "..", "frontend", "js", "practical-environment.js"), "utf8"))(mod, undefined);
const P = mod.exports;

const results = [];
function test(name, fn) { try { fn(); results.push([name, "PASS", null]); } catch (e) { results.push([name, "FAIL", e.stack || String(e)]); } }

test("selected cells are summarised like a spreadsheet status bar", () => {
    const s = P.summarise(["1,200", "650", "Rs 900", "North"]);
    assert.strictEqual(s.cells, 4);
    assert.strictEqual(s.numbers, 3);
    assert.strictEqual(s.sum, 2750);
    assert.ok(Math.abs(s.average - 916.6667) < 0.001);
    assert.strictEqual(s.min, 650);
    assert.strictEqual(s.max, 1200);
});

test("the calculator evaluates arithmetic and refuses anything else", () => {
    assert.strictEqual(P.calculate("(1200*5) + 340"), 6340);
    assert.strictEqual(P.calculate("1,200 / 4"), 300);
    assert.strictEqual(P.calculate("alert(1)"), null);
    assert.strictEqual(P.calculate("1/0"), null);
    assert.strictEqual(P.calculate("process.exit()"), null);
    assert.strictEqual(P.calculate("2**2"), 4);                       // digits and operators only: still pure arithmetic
});

test("numbers are read from messy input", () => {
    assert.strictEqual(P.parseNumber("Rs 1,23,456.50"), 123456.5);
    assert.strictEqual(P.parseNumber("-450"), -450);
    assert.strictEqual(P.parseNumber("abc"), null);
    assert.strictEqual(P.parseNumber(""), null);
});

test("words are counted", () => {
    assert.strictEqual(P.countWords("Dear Mr. Rao, I'm writing to you"), 7);
    assert.strictEqual(P.countWords(""), 0);
});

const written = {
    kind: "practical",
    practical: { environment: "written" },
    questions: [{ id: 1, type: "written", question: "Reply <b>now</b>", scenario: "A customer is upset.\nThe parcel is late.", instructions: "Write the email.", min_words: 50, max_words: 150 }],
};

test("a written task shows the situation, the instruction and a text box, with the draft restored and HTML escaped", () => {
    const html = P.renderHtml(written, new Map([["1", "My <draft>"]]));
    assert.ok(html.includes("textarea"));
    assert.ok(html.includes("A customer is upset.<br>The parcel is late."));
    assert.ok(html.includes("Write the email."));
    assert.ok(html.includes("My &lt;draft&gt;"));
    assert.ok(!html.includes("<b>now</b>"));
    assert.ok(html.includes("aim for 50 to 150"));
});

const dataTest = {
    kind: "practical",
    practical: { environment: "data", dataset: { title: "Sales", note: "Use every row.", columns: ["Region", "Units"], rows: [["North", 5], ["South", 7]] } },
    questions: [{ id: 1, type: "data", question: "Total units?", answer_kind: "number", unit: "units", hint: "" }],
};

test("a data task shows the table tools and one input per question", () => {
    const html = P.renderHtml(dataTest, new Map());
    assert.ok(html.includes("practicalBody") && html.includes("practicalFilter") && html.includes("practicalCalc"));
    assert.ok(html.includes('id="answer_1"') && html.includes("Total units?"));
    assert.ok(html.includes('data-sort="0"') && html.includes('data-pick="1"'));
});

test("nothing that looks like an answer key is ever rendered", () => {
    const html = P.renderHtml({ ...dataTest, questions: [{ ...dataTest.questions[0] }] }, new Map());
    assert.ok(!/correct_answer|tolerance|rubric/.test(html));
});

let failed = 0;
for (const [name, status, detail] of results) {
    console.log(`${status}   ${name}`);
    if (detail) { failed++; console.log(detail); }
}
console.log("=".repeat(70));
console.log(`${results.length - failed} passed, ${failed} failed, ${results.length} total`);
process.exit(failed ? 1 : 0);
