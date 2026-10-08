/* =========================================================
   HIREFLOW AI - PRACTICAL ASSESSMENT ENVIRONMENTS

   The candidate-facing side of the "practical" stage-3 test (see backend practical_*.py). Two environments:

     written  a work situation (a customer email, a brief...) and a text box: the candidate writes the piece of work.
     data     a table on screen and calculation questions. The page gives Excel-like helpers (sort, filter, select cells to see
              count / sum / average, a small calculator); the answers are checked by the server against a key computed from the table.

   Used by sjt-test.js. Pure rendering + collecting: it never talks to the server and never sees an answer key (the server does not send it).
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.PracticalEnvironment = factory();
})(typeof self !== "undefined" ? self : this, function () {

    "use strict";

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function countWords(text) {
        const found = String(text || "").match(/[\w'-]+/g);
        return found ? found.length : 0;
    }

    function parseNumber(text) {
        const raw = String(text == null ? "" : text).trim();
        if (raw === "") return null;
        const cleaned = raw.replace(/,/g, "").replace(/[^0-9.\-]/g, "");
        if (cleaned === "" || cleaned === "-" || cleaned === ".") return null;
        const value = Number(cleaned);
        return Number.isFinite(value) ? value : null;
    }

    // The statistics shown for the selected cells (like the status bar of a spreadsheet).
    function summarise(values) {
        const numbers = values.map(parseNumber).filter(function (n) { return n !== null; });
        const sum = numbers.reduce(function (a, b) { return a + b; }, 0);
        return {
            cells: values.length,
            numbers: numbers.length,
            sum: sum,
            average: numbers.length ? sum / numbers.length : null,
            min: numbers.length ? Math.min.apply(null, numbers) : null,
            max: numbers.length ? Math.max.apply(null, numbers) : null,
        };
    }

    // A calculator that only accepts digits, + - * / ( ) and the decimal point: never evaluates arbitrary code.
    function calculate(expression) {
        const text = String(expression || "").replace(/\s+/g, "").replace(/,/g, "");
        if (!/^[0-9+\-*/().]+$/.test(text) || text.length > 200) return null;
        try {
            const value = new Function("return (" + text + ");")();
            return typeof value === "number" && Number.isFinite(value) ? value : null;
        } catch (error) {
            return null;
        }
    }

    function format(value) {
        if (value === null || value === undefined) return "-";
        return Number.isInteger(value) ? value.toLocaleString("en-IN") : value.toLocaleString("en-IN", { maximumFractionDigits: 4 });
    }

    // ---------------------------------------------------------------- rendering

    function writtenHtml(question, index, total, draft) {
        const words = countWords(draft);
        return `
            <div class="question-card practical-card" data-question-id="${question.id}">
                <div class="question-number">Task ${index + 1} of ${total}: ${esc(question.question)}</div>
                <div class="practical-scenario">${esc(question.scenario).replace(/\n/g, "<br>")}</div>
                <div class="practical-instructions"><strong>Your task:</strong> ${esc(question.instructions)}</div>
                <textarea id="answer_${question.id}" class="form-control practical-text" rows="9" maxlength="6000"
                    placeholder="Write your answer here..." data-question-id="${question.id}">${esc(draft)}</textarea>
                <div class="practical-counter small text-muted" id="count_${question.id}">
                    ${words} words &middot; aim for ${question.min_words} to ${question.max_words}
                </div>
            </div>`;
    }

    function dataQuestionHtml(question, index, total, draft) {
        const unit = question.unit ? `<span class="practical-unit">${esc(question.unit)}</span>` : "";
        const hint = question.hint ? `<div class="small text-muted mt-1">${esc(question.hint)}</div>` : "";
        return `
            <div class="question-card practical-card" data-question-id="${question.id}">
                <div class="question-number">Question ${index + 1} of ${total}</div>
                <div class="question-text">${esc(question.question)}</div>
                <div class="practical-answer-row">
                    <input type="text" id="answer_${question.id}" class="form-control practical-input" autocomplete="off"
                        inputmode="${question.answer_kind === "number" ? "decimal" : "text"}"
                        placeholder="${question.answer_kind === "number" ? "Type a number" : "Type your answer"}"
                        value="${esc(draft)}" data-question-id="${question.id}">
                    ${unit}
                </div>
                ${hint}
            </div>`;
    }

    function labHtml(artifact) {

        const lines = (artifact.lines || []).map(function (line, i) {
            return `<div class="lab-line" data-line="${i + 1}"><span class="lab-no">${i + 1}</span><span class="lab-text">${esc(line) || "&nbsp;"}</span></div>`;
        }).join("");

        return `
            <div class="practical-sheet">
                <div class="practical-sheet-head"><div><strong>${esc(artifact.title)}</strong> <span class="small text-muted">(${esc(artifact.name)})</span>
                    <div class="small text-muted">${esc(artifact.note)}</div></div></div>
                <div class="practical-tools">
                    <input type="search" id="labFilter" class="form-control form-control-sm" placeholder="Search the text (shows only matching lines)" aria-label="Search the text">
                    <span class="small text-muted" id="labCount"></span>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="labClear">Clear marks</button>
                </div>
                <div class="practical-scroll lab-scroll" id="labBody">${lines}</div>
                <div class="practical-status small" id="labStatus" role="status" aria-live="polite">Click a line to mark it. Line numbers are shown on the left.</div>
                <div class="practical-calc"><label class="small text-muted" for="practicalCalc">Calculator</label><input type="text" id="practicalCalc" class="form-control form-control-sm" placeholder="e.g. 4096 - 2" autocomplete="off"><span class="small" id="practicalCalcResult"></span></div>
            </div>`;
    }

    function datasetHtml(dataset) {
        const head = dataset.columns.map(function (column, c) {
            return `<th data-column="${c}"><button type="button" class="practical-sort" data-sort="${c}" title="Sort by ${esc(column)}">${esc(column)} <span aria-hidden="true">&#8693;</span></button>
                    <button type="button" class="practical-pick" data-pick="${c}" title="Select every visible cell in this column" aria-label="Select column ${esc(column)}">&Sigma;</button></th>`;
        }).join("");

        return `
            <div class="practical-sheet">
                <div class="practical-sheet-head">
                    <div><strong>${esc(dataset.title)}</strong><div class="small text-muted">${esc(dataset.note)}</div></div>
                </div>
                <div class="practical-tools">
                    <input type="search" id="practicalFilter" class="form-control form-control-sm" placeholder="Filter rows (type any text)" aria-label="Filter rows">
                    <span class="small text-muted" id="practicalRowCount"></span>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="practicalClear">Clear selection</button>
                </div>
                <div class="practical-scroll"><table class="practical-table"><thead><tr>${head}</tr></thead><tbody id="practicalBody"></tbody></table></div>
                <div class="practical-status small" id="practicalStatus" role="status" aria-live="polite">Click cells (or the &Sigma; button on a column) to see their count, sum and average.</div>
                <div class="practical-calc">
                    <label class="small text-muted" for="practicalCalc">Calculator</label>
                    <input type="text" id="practicalCalc" class="form-control form-control-sm" placeholder="e.g. (1200*5) + 340" autocomplete="off">
                    <span class="small" id="practicalCalcResult"></span>
                </div>
            </div>`;
    }

    function renderHtml(test, drafts) {
        const draftById = drafts instanceof Map ? drafts : new Map();
        const questions = test.questions || [];
        const environment = test.practical && test.practical.environment;

        if (environment === "design") {
            return window.PracticalDesign ? window.PracticalDesign.html(test, draftById.get("1") || "") : '<p class="text-danger">The design editor could not load. Please reload the page.</p>';
        }

        if (environment === "roleplay") {
            return window.PracticalRoleplay ? window.PracticalRoleplay.html(test) : '<p class="text-danger">The call simulation could not load. Please reload the page.</p>';
        }

        const tasks = questions.map(function (q, i) {
            const draft = draftById.get(String(q.id)) || "";
            return q.type === "written" ? writtenHtml(q, i, questions.length, draft) : dataQuestionHtml(q, i, questions.length, draft);
        }).join("");

        if (environment === "lab" && test.practical.artifact) {
            return `<div class="practical-layout">${labHtml(test.practical.artifact)}<div class="practical-questions">${tasks}</div></div>`;
        }

        if (environment === "data" && test.practical.dataset) {
            return `<div class="practical-layout">${datasetHtml(test.practical.dataset)}<div class="practical-questions">${tasks}</div></div>`;
        }

        return tasks;
    }

    // ---------------------------------------------------------------- behaviour (needs a DOM)

    function init(container, test, onChange) {

        const environment = test.practical && test.practical.environment;

        if (environment === "roleplay") {
            if (window.PracticalRoleplay) window.PracticalRoleplay.mount(container, test, {});
            return;
        }

        if (environment === "design") {
            if (window.PracticalDesign) window.PracticalDesign.mount(container, test, onChange);
            return;
        }

        container.addEventListener("input", function (event) {
            const target = event.target;
            if (!target || !target.matches) return;

            if (target.matches("textarea.practical-text")) {
                const counter = container.querySelector("#count_" + target.dataset.questionId);
                const question = (test.questions || []).find(function (q) { return String(q.id) === target.dataset.questionId; }) || {};
                if (counter) counter.innerHTML = countWords(target.value) + " words &middot; aim for " + question.min_words + " to " + question.max_words;
            }

            if (target.matches(".practical-text, .practical-input") && typeof onChange === "function") onChange();
        });

        if (environment === "lab" && test.practical.artifact) {

            const body = container.querySelector("#labBody");
            const filter = container.querySelector("#labFilter");
            const count = container.querySelector("#labCount");
            const status = container.querySelector("#labStatus");
            const calc = container.querySelector("#practicalCalc");
            const calcResult = container.querySelector("#practicalCalcResult");
            const lines = test.practical.artifact.lines || [];
            const marked = new Set();

            function paint() {
                const needle = (filter.value || "").trim().toLowerCase();
                let shown = 0;
                body.querySelectorAll(".lab-line").forEach(function (node) {
                    const n = Number(node.dataset.line);
                    const match = !needle || String(lines[n - 1]).toLowerCase().indexOf(needle) !== -1;
                    node.style.display = match ? "" : "none";
                    node.classList.toggle("is-marked", marked.has(n));
                    if (match) shown++;
                });
                count.textContent = needle ? shown + " of " + lines.length + " lines match" : lines.length + " lines";
                status.textContent = marked.size ? "Marked lines: " + Array.from(marked).sort(function (a, b) { return a - b; }).join(", ") : "Click a line to mark it. Line numbers are shown on the left.";
            }

            body.addEventListener("click", function (event) {
                const node = event.target.closest && event.target.closest(".lab-line");
                if (!node) return;
                const n = Number(node.dataset.line);
                marked.has(n) ? marked.delete(n) : marked.add(n);
                paint();
            });

            container.querySelector("#labClear").addEventListener("click", function () { marked.clear(); paint(); });
            filter.addEventListener("input", paint);
            calc.addEventListener("input", function () {
                if (!calc.value.trim()) { calcResult.textContent = ""; return; }
                const value = calculate(calc.value);
                calcResult.textContent = value === null ? "?" : "= " + format(value);
            });

            paint();
            return;
        }

        if (environment !== "data" || !test.practical.dataset) return;

        const dataset = test.practical.dataset;
        const body = container.querySelector("#practicalBody");
        const filter = container.querySelector("#practicalFilter");
        const rowCount = container.querySelector("#practicalRowCount");
        const status = container.querySelector("#practicalStatus");
        const calc = container.querySelector("#practicalCalc");
        const calcResult = container.querySelector("#practicalCalcResult");

        let order = dataset.rows.map(function (_row, i) { return i; });
        let sortColumn = null;
        let sortAscending = true;
        const selected = new Set();                                  // "row:column" of the original data

        function visibleIndices() {
            const needle = (filter.value || "").trim().toLowerCase();
            return order.filter(function (i) {
                return !needle || dataset.rows[i].some(function (cell) { return String(cell).toLowerCase().indexOf(needle) !== -1; });
            });
        }

        function updateStatus() {
            if (!selected.size) {
                status.innerHTML = "Click cells (or the &Sigma; button on a column) to see their count, sum and average.";
                return;
            }
            const values = Array.from(selected).map(function (key) {
                const parts = key.split(":");
                return dataset.rows[Number(parts[0])][Number(parts[1])];
            });
            const s = summarise(values);
            status.innerHTML = "<strong>Selected:</strong> " + s.cells + " cells &middot; Count (numbers) " + s.numbers +
                " &middot; Sum " + format(s.sum) + " &middot; Average " + format(s.average) + " &middot; Min " + format(s.min) + " &middot; Max " + format(s.max);
        }

        function draw() {
            const visible = visibleIndices();
            body.innerHTML = visible.map(function (i) {
                return "<tr>" + dataset.rows[i].map(function (cell, c) {
                    const key = i + ":" + c;
                    return `<td data-key="${key}" class="${selected.has(key) ? "is-selected" : ""}">${esc(cell)}</td>`;
                }).join("") + "</tr>";
            }).join("");
            rowCount.textContent = "Showing " + visible.length + " of " + dataset.rows.length + " rows";
        }

        function sortBy(column) {
            sortAscending = sortColumn === column ? !sortAscending : true;
            sortColumn = column;
            order = dataset.rows.map(function (_row, i) { return i; }).sort(function (a, b) {
                const x = dataset.rows[a][column], y = dataset.rows[b][column];
                const nx = parseNumber(x), ny = parseNumber(y);
                const result = nx !== null && ny !== null ? nx - ny : String(x).localeCompare(String(y));
                return sortAscending ? result : -result;
            });
            draw();
        }

        container.addEventListener("click", function (event) {
            const target = event.target;
            if (!target || !target.closest) return;

            const sorter = target.closest("[data-sort]");
            if (sorter) { sortBy(Number(sorter.dataset.sort)); return; }

            const picker = target.closest("[data-pick]");
            if (picker) {
                const column = Number(picker.dataset.pick);
                const keys = visibleIndices().map(function (i) { return i + ":" + column; });
                const allSelected = keys.every(function (key) { return selected.has(key); });
                keys.forEach(function (key) { allSelected ? selected.delete(key) : selected.add(key); });
                draw(); updateStatus();
                return;
            }

            const cell = target.closest("td[data-key]");
            if (cell) {
                const key = cell.dataset.key;
                selected.has(key) ? selected.delete(key) : selected.add(key);
                cell.classList.toggle("is-selected", selected.has(key));
                updateStatus();
                return;
            }

            if (target.closest("#practicalClear")) { selected.clear(); draw(); updateStatus(); }
        });

        filter.addEventListener("input", draw);

        calc.addEventListener("input", function () {
            if (!calc.value.trim()) { calcResult.textContent = ""; return; }
            const value = calculate(calc.value);
            calcResult.textContent = value === null ? "?" : "= " + format(value);
        });

        draw();
        updateStatus();
    }

    // ---------------------------------------------------------------- collecting answers

    function collect(container, question) {
        if (question.type === "design") return window.PracticalDesign ? window.PracticalDesign.collect(question) : { question_id: question.id, selected_answer: null };
        if (question.type === "roleplay") return { question_id: question.id, selected_answer: null };      // the call itself is the answer (kept on the server)
        const field = container.querySelector("#answer_" + question.id);
        const text = field ? field.value : "";
        return { question_id: question.id, selected_answer: text.trim() === "" ? null : text };
    }

    function isAnswered(container, question) {
        if (question.type === "design") return !!(window.PracticalDesign && window.PracticalDesign.isAnswered());
        if (question.type === "roleplay") return !!(window.PracticalRoleplay && window.PracticalRoleplay.hasHeldCall());
        const field = container.querySelector("#answer_" + question.id);
        return !!(field && field.value.trim() !== "");
    }

    return { renderHtml: renderHtml, init: init, collect: collect, isAnswered: isAnswered, summarise: summarise, calculate: calculate, parseNumber: parseNumber, countWords: countWords };
});
