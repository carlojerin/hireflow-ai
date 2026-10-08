/* =========================================================
   HIREFLOW AI - PRACTICAL WEB DESIGN ENVIRONMENT

   The candidate designs a web page from a brief by dragging components (navigation bar, heading, text, button, image, card, input, footer,
   section background, divider) onto a 1200 px canvas, moving and resizing them, and styling them in a properties panel. The finished layout is
   sent as JSON; the server measures it and the AI marks it (backend practical_design.py).

   Three layers in one file:
     createState()    the layout and its history (add, move, resize, style, order, undo / redo): pure, tested in node
     renderStatic()   draws a stored layout read-only (used by the HR report to show the candidate's page)
     mount()          the editor in the browser (palette, canvas, properties panel, keyboard)
   ========================================================= */

(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory();
    else root.PracticalDesign = factory();
})(typeof self !== "undefined" ? self : this, function () {

    "use strict";

    const GRID = 8;
    const CANVAS_W = 1200;
    const CANVAS_H = 1500;
    const MAX_ELEMENTS = 70;
    const MIN_ELEMENTS_TO_SUBMIT = 4;
    const HISTORY_LIMIT = 60;

    const TYPES = {
        nav:     { label: "Navigation bar", w: 1200, h: 72,  text: "Navigation", fontSize: 16, fontWeight: 700, color: "#ffffff", bg: "#1f2937", align: "left", radius: 0 },
        section: { label: "Section background", w: 1200, h: 320, text: "", fontSize: 16, fontWeight: 400, color: "#111827", bg: "#eef1ff", align: "left", radius: 0 },
        heading: { label: "Heading", w: 520, h: 56, text: "Heading text", fontSize: 40, fontWeight: 700, color: "#111827", bg: "", align: "left", radius: 0 },
        text:    { label: "Paragraph", w: 480, h: 80, text: "Paragraph text", fontSize: 16, fontWeight: 400, color: "#374151", bg: "", align: "left", radius: 0 },
        button:  { label: "Button", w: 200, h: 52, text: "Button", fontSize: 16, fontWeight: 700, color: "#ffffff", bg: "#4f5bff", align: "center", radius: 10 },
        image:   { label: "Image", w: 400, h: 260, text: "Image", fontSize: 14, fontWeight: 400, color: "#374151", bg: "#d1d5db", align: "center", radius: 8 },
        card:    { label: "Card", w: 340, h: 200, text: "Card title and short text", fontSize: 16, fontWeight: 400, color: "#111827", bg: "#ffffff", align: "left", radius: 14 },
        input:   { label: "Input field", w: 320, h: 48, text: "Your email", fontSize: 15, fontWeight: 400, color: "#4b5563", bg: "#ffffff", align: "left", radius: 8 },
        footer:  { label: "Footer", w: 1200, h: 120, text: "Footer", fontSize: 14, fontWeight: 400, color: "#e5e7eb", bg: "#111827", align: "center", radius: 0 },
        divider: { label: "Divider line", w: 600, h: 2, text: "", fontSize: 16, fontWeight: 400, color: "#111827", bg: "#d1d5db", align: "left", radius: 0 },
    };

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function snap(value) { return Math.round(value / GRID) * GRID; }

    function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }

    // ---------------------------------------------------------------- the layout and its history

    function createState(initialElements) {

        let elements = (initialElements || []).map(function (e) { return Object.assign({}, e); });
        let nextId = elements.reduce(function (m, e) { return Math.max(m, e.id || 0); }, 0) + 1;
        let selectedId = null;
        const undoStack = [];
        let redoStack = [];

        function snapshot() { return JSON.stringify(elements); }

        // call BEFORE a change (or once at the start of a drag): remembers the state to go back to
        function checkpoint() {
            undoStack.push(snapshot());
            if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
            redoStack = [];
        }

        function find(id) { return elements.find(function (e) { return e.id === id; }) || null; }

        function add(type, x, y) {

            if (!TYPES[type] || elements.length >= MAX_ELEMENTS) return null;

            checkpoint();

            const base = TYPES[type];
            const element = Object.assign({ id: nextId++, type: type, x: 0, y: 0 }, {
                w: base.w, h: base.h, text: base.text, fontSize: base.fontSize, fontWeight: base.fontWeight, color: base.color, bg: base.bg, radius: base.radius, align: base.align,
            });

            element.x = clamp(snap(x == null ? 0 : x), 0, CANVAS_W - Math.min(element.w, CANVAS_W));
            element.y = clamp(snap(y == null ? 0 : y), 0, CANVAS_H - element.h);

            elements.push(element);
            selectedId = element.id;

            return element;
        }

        function move(id, x, y) {
            const e = find(id);
            if (!e) return;
            e.x = clamp(snap(x), -e.w + 40, CANVAS_W - 40);
            e.y = clamp(snap(y), 0, CANVAS_H + 200);
        }

        function resize(id, w, h) {
            const e = find(id);
            if (!e) return;
            e.w = clamp(snap(w), 16, CANVAS_W);
            e.h = clamp(snap(h), 2, CANVAS_H);
        }

        function update(id, patch) {
            const e = find(id);
            if (!e) return;
            Object.keys(patch).forEach(function (key) { e[key] = patch[key]; });
        }

        function remove(id) {
            if (!find(id)) return;
            checkpoint();
            elements = elements.filter(function (e) { return e.id !== id; });
            if (selectedId === id) selectedId = null;
        }

        function duplicate(id) {
            const e = find(id);
            if (!e || elements.length >= MAX_ELEMENTS) return null;
            checkpoint();
            const copy = Object.assign({}, e, { id: nextId++, x: e.x + 16, y: e.y + 16 });
            elements.push(copy);
            selectedId = copy.id;
            return copy;
        }

        function reorder(id, direction) {
            const index = elements.findIndex(function (e) { return e.id === id; });
            const target = direction === "forward" ? index + 1 : index - 1;
            if (index < 0 || target < 0 || target >= elements.length) return;
            checkpoint();
            const moved = elements.splice(index, 1)[0];
            elements.splice(target, 0, moved);
        }

        function undo() {
            if (!undoStack.length) return false;
            redoStack.push(snapshot());
            elements = JSON.parse(undoStack.pop());
            if (selectedId !== null && !find(selectedId)) selectedId = null;
            return true;
        }

        function redo() {
            if (!redoStack.length) return false;
            undoStack.push(snapshot());
            elements = JSON.parse(redoStack.pop());
            if (selectedId !== null && !find(selectedId)) selectedId = null;
            return true;
        }

        return {
            checkpoint: checkpoint, add: add, move: move, resize: resize, update: update, remove: remove, duplicate: duplicate, reorder: reorder, undo: undo, redo: redo,
            get: find, select: function (id) { selectedId = id; }, selected: function () { return find(selectedId); }, selectedId: function () { return selectedId; },
            elements: function () { return elements; }, count: function () { return elements.length; },
            toLayout: function () { return { elements: elements.map(function (e) { return Object.assign({}, e); }) }; },
            toJSON: function () { return JSON.stringify({ elements: elements }); },
        };
    }

    function parseLayout(text) {
        try {
            const data = typeof text === "string" ? JSON.parse(text) : text;
            return data && Array.isArray(data.elements) ? data.elements.filter(function (e) { return e && TYPES[e.type]; }) : [];
        } catch (error) {
            return [];
        }
    }

    // ---------------------------------------------------------------- drawing one element (the editor and the HR preview look the same)

    function elementStyle(e) {

        const base = "position:absolute;box-sizing:border-box;left:" + e.x + "px;top:" + e.y + "px;width:" + e.w + "px;height:" + e.h + "px;" +
            "font-size:" + e.fontSize + "px;font-weight:" + e.fontWeight + ";color:" + e.color + ";text-align:" + e.align + ";" +
            "border-radius:" + e.radius + "px;overflow:hidden;white-space:pre-wrap;line-height:1.3;font-family:'DM Sans',system-ui,sans-serif;";

        const fill = e.bg ? "background:" + e.bg + ";" : "";
        let look = "";

        if (e.type === "nav" || e.type === "footer") look = "display:flex;align-items:center;padding:0 24px;" + (e.align === "center" ? "justify-content:center;" : e.align === "right" ? "justify-content:flex-end;" : "");
        else if (e.type === "button") look = "display:flex;align-items:center;justify-content:center;";
        else if (e.type === "image") look = "display:flex;align-items:center;justify-content:center;border:2px dashed rgba(0,0,0,.18);";
        else if (e.type === "card") look = "padding:16px;box-shadow:0 6px 18px rgba(20,24,58,.12);border:1px solid rgba(20,24,58,.08);";
        else if (e.type === "input") look = "display:flex;align-items:center;padding:0 14px;border:1px solid rgba(20,24,58,.25);";
        else if (e.type === "heading" || e.type === "text") look = "";

        return base + fill + look;
    }

    function elementHtml(e, selected, interactive) {
        const label = e.type === "image" ? "&#9638; " + esc(e.text) : esc(e.text);
        return `<div class="pd-el pd-${e.type}${selected ? " is-selected" : ""}" data-id="${e.id}" style="${elementStyle(e)}"${interactive ? ' tabindex="0"' : ""}>${label}${selected && interactive ? '<span class="pd-handle" data-handle="se"></span>' : ""}</div>`;
    }

    // a stored layout, read-only, scaled to a width (the HR report)
    function renderStatic(layout, target, width) {

        const elements = parseLayout(layout);
        const bottom = Math.max(400, Math.min(CANVAS_H + 200, elements.reduce(function (m, e) { return Math.max(m, e.y + e.h); }, 0) + 40));
        const scale = Math.min(1, (width || 700) / CANVAS_W);

        target.innerHTML = `<div class="pd-static" style="width:${Math.round(CANVAS_W * scale)}px;height:${Math.round(bottom * scale)}px;">` +
            `<div class="pd-canvas pd-readonly" style="width:${CANVAS_W}px;height:${bottom}px;transform:scale(${scale});transform-origin:top left;">` +
            elements.map(function (e) { return elementHtml(e, false, false); }).join("") + "</div></div>";
    }

    // ---------------------------------------------------------------- the editor page

    let initialElements = [];

    function html(test, draft) {

        const design = (test.practical && test.practical.design) || {};
        const brand = design.brand || {};
        const copy = design.copy || {};

        initialElements = parseLayout(draft);

        const copyChips = [copy.headline, copy.subhead, copy.cta].concat(copy.features || []).concat([copy.testimonial]).filter(Boolean)
            .map(function (line) { return `<button type="button" class="pd-chip" data-copy="${esc(line)}">${esc(line)}</button>`; }).join("");

        const palette = Object.keys(TYPES).map(function (type) {
            return `<button type="button" class="pd-palette-item" draggable="true" data-type="${type}" title="Drag onto the page, or click to add">${esc(TYPES[type].label)}</button>`;
        }).join("");

        return `
            <div class="question-card practical-card pd-brief">
                <div class="question-number">${esc(design.title || "Web design task")}</div>
                <p class="mb-2">${esc(design.brief || "")}</p>
                <div class="small text-muted mb-2"><strong>Audience:</strong> ${esc(design.audience || "")} &middot; <strong>Brand:</strong> ${esc(brand.name || "")}
                    <span class="pd-swatch" style="background:${esc(brand.primary || "#4f5bff")}"></span> ${esc(brand.primary || "")} &middot; <strong>Tone:</strong> ${esc(brand.tone || "")}</div>
                <div class="pd-columns">
                    <div><div class="small fw-semibold mb-1">Your page must have</div><ul class="small mb-2">${(design.checklist || []).map(function (c) { return "<li>" + esc(c) + "</li>"; }).join("")}</ul></div>
                    <div><div class="small fw-semibold mb-1">Copy you can use (select an element, then click a line)</div><div class="pd-chips">${copyChips}</div></div>
                </div>
            </div>

            <div class="pd-editor">
                <div class="pd-toolbar" role="toolbar" aria-label="Design tools">
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="pdUndo">Undo</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="pdRedo">Redo</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="pdForward">Bring forward</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="pdBack">Send back</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" id="pdDuplicate">Duplicate</button>
                    <button type="button" class="btn btn-sm btn-outline-danger" id="pdDelete">Delete</button>
                    <span class="small text-muted ms-auto" id="pdCount"></span>
                </div>
                <div class="pd-body">
                    <div class="pd-palette"><div class="small fw-semibold mb-2">Components</div>${palette}
                        <div class="small text-muted mt-2">Drag a component onto the page. Move it by dragging, resize with the corner handle. Arrow keys nudge the selected element.</div></div>
                    <div class="pd-stage" id="pdStage"><div class="pd-canvas-wrap" id="pdWrap"><div class="pd-canvas" id="pdCanvas" style="width:${CANVAS_W}px;height:${CANVAS_H}px;"></div></div></div>
                    <div class="pd-props" id="pdProps"></div>
                </div>
            </div>`;
    }

    function mount(container, test, onChange) {

        const state = createState(initialElements);
        const canvas = container.querySelector("#pdCanvas");
        const wrap = container.querySelector("#pdWrap");
        const stage = container.querySelector("#pdStage");
        const props = container.querySelector("#pdProps");
        const count = container.querySelector("#pdCount");

        let scale = 1;

        function fit() {
            scale = Math.min(1, Math.max(0.3, (stage.clientWidth || CANVAS_W) / CANVAS_W));
            canvas.style.transform = "scale(" + scale + ")";
            canvas.style.transformOrigin = "top left";
            wrap.style.width = Math.round(CANVAS_W * scale) + "px";
            wrap.style.height = Math.round(CANVAS_H * scale) + "px";
        }

        function changed() { if (typeof onChange === "function") onChange(); }

        function drawProps() {

            const e = state.selected();

            if (!e) {
                props.innerHTML = '<div class="small text-muted">Select an element to edit its text and style.</div>';
                return;
            }

            const hasText = e.type !== "divider" && e.type !== "section";

            props.innerHTML = `
                <div class="small fw-semibold mb-2">${esc(TYPES[e.type].label)}</div>
                ${hasText ? `<label class="pd-label" for="pdText">Text</label><textarea id="pdText" class="form-control form-control-sm" rows="3" maxlength="300">${esc(e.text)}</textarea>` : ""}
                ${hasText ? `<div class="pd-row"><div><label class="pd-label" for="pdSize">Size</label><input id="pdSize" type="number" min="8" max="96" class="form-control form-control-sm" value="${e.fontSize}"></div>
                    <div><label class="pd-label" for="pdWeight">Weight</label><select id="pdWeight" class="form-select form-select-sm"><option value="400"${e.fontWeight < 600 ? " selected" : ""}>Regular</option><option value="700"${e.fontWeight >= 600 ? " selected" : ""}>Bold</option></select></div>
                    <div><label class="pd-label" for="pdAlign">Align</label><select id="pdAlign" class="form-select form-select-sm"><option value="left"${e.align === "left" ? " selected" : ""}>Left</option><option value="center"${e.align === "center" ? " selected" : ""}>Center</option><option value="right"${e.align === "right" ? " selected" : ""}>Right</option></select></div></div>` : ""}
                <div class="pd-row">
                    ${hasText ? `<div><label class="pd-label" for="pdColor">Text colour</label><input id="pdColor" type="color" class="form-control form-control-color" value="${e.color}"></div>` : ""}
                    <div><label class="pd-label" for="pdBg">Fill</label><input id="pdBg" type="color" class="form-control form-control-color" value="${e.bg || "#ffffff"}"></div>
                    <div><label class="pd-label" for="pdNoFill">No fill</label><input id="pdNoFill" type="checkbox" class="form-check-input d-block mt-2"${e.bg ? "" : " checked"}></div>
                </div>
                <label class="pd-label" for="pdRadius">Corner radius</label><input id="pdRadius" type="range" min="0" max="40" class="form-range" value="${e.radius}">
                <div class="small text-muted">${e.w} x ${e.h} px at ${e.x}, ${e.y}</div>`;
        }

        function draw() {
            const selected = state.selectedId();
            canvas.innerHTML = state.elements().map(function (e) { return elementHtml(e, e.id === selected, true); }).join("");
            count.textContent = state.count() + " element" + (state.count() === 1 ? "" : "s") + " (at least " + MIN_ELEMENTS_TO_SUBMIT + " needed)";
            drawProps();
        }

        function canvasPoint(event) {
            const rect = canvas.getBoundingClientRect();
            return { x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale };
        }

        // ------- adding: drag from the palette, or click an item
        container.querySelectorAll(".pd-palette-item").forEach(function (item) {
            item.addEventListener("dragstart", function (event) { event.dataTransfer.setData("text/pd-type", item.dataset.type); event.dataTransfer.effectAllowed = "copy"; });
            item.addEventListener("click", function () {
                const last = state.elements()[state.count() - 1];
                state.add(item.dataset.type, 40, last ? Math.min(CANVAS_H - 120, last.y + last.h + 24) : 40);
                draw(); changed();
            });
        });

        canvas.addEventListener("dragover", function (event) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
        canvas.addEventListener("drop", function (event) {
            event.preventDefault();
            const type = event.dataTransfer.getData("text/pd-type");
            if (!TYPES[type]) return;
            const p = canvasPoint(event);
            state.add(type, p.x - TYPES[type].w / 2, p.y - TYPES[type].h / 2);
            draw(); changed();
        });

        // ------- moving and resizing
        let gesture = null;

        canvas.addEventListener("pointerdown", function (event) {

            const node = event.target.closest(".pd-el");

            if (!node) { state.select(null); draw(); return; }

            const id = Number(node.dataset.id);
            const handle = event.target.closest(".pd-handle");
            const e = state.get(id);
            const p = canvasPoint(event);

            if (state.selectedId() !== id) { state.select(id); draw(); }

            state.checkpoint();
            gesture = { id: id, mode: handle ? "resize" : "move", startX: p.x, startY: p.y, ox: e.x, oy: e.y, ow: e.w, oh: e.h, moved: false };

            try { canvas.setPointerCapture(event.pointerId); } catch (error) { /* not needed */ }
            event.preventDefault();
        });

        canvas.addEventListener("pointermove", function (event) {

            if (!gesture) return;

            const p = canvasPoint(event);
            const dx = p.x - gesture.startX, dy = p.y - gesture.startY;

            gesture.moved = gesture.moved || Math.abs(dx) > 2 || Math.abs(dy) > 2;

            if (gesture.mode === "move") state.move(gesture.id, gesture.ox + dx, gesture.oy + dy);
            else state.resize(gesture.id, gesture.ow + dx, gesture.oh + dy);

            const e = state.get(gesture.id);
            const node = canvas.querySelector('[data-id="' + gesture.id + '"]');
            if (e && node) { node.style.left = e.x + "px"; node.style.top = e.y + "px"; node.style.width = e.w + "px"; node.style.height = e.h + "px"; }
        });

        function endGesture() {
            if (!gesture) return;
            const moved = gesture.moved;
            gesture = null;
            if (moved) { draw(); changed(); }
        }

        canvas.addEventListener("pointerup", endGesture);
        canvas.addEventListener("pointercancel", endGesture);

        // ------- keyboard (only when the page area has focus, never while typing in a field)
        container.addEventListener("keydown", function (event) {

            const typing = event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
            const e = state.selected();

            if ((event.ctrlKey || event.metaKey) && !typing && (event.key === "z" || event.key === "y")) {
                event.preventDefault();
                (event.key === "z" ? state.undo() : state.redo()); draw(); changed();
                return;
            }

            if (typing || !e) return;

            if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); state.remove(e.id); draw(); changed(); }
            else if (event.key.indexOf("Arrow") === 0) {
                event.preventDefault();
                state.checkpoint();
                state.move(e.id, e.x + (event.key === "ArrowRight" ? GRID : event.key === "ArrowLeft" ? -GRID : 0), e.y + (event.key === "ArrowDown" ? GRID : event.key === "ArrowUp" ? -GRID : 0));
                draw(); changed();
            }
        });

        // ------- toolbar
        function act(id, fn) {
            const button = container.querySelector(id);
            if (button) button.addEventListener("click", function () { fn(); draw(); changed(); });
        }

        act("#pdUndo", function () { state.undo(); });
        act("#pdRedo", function () { state.redo(); });
        act("#pdDelete", function () { if (state.selected()) state.remove(state.selectedId()); });
        act("#pdDuplicate", function () { if (state.selected()) state.duplicate(state.selectedId()); });
        act("#pdForward", function () { if (state.selected()) state.reorder(state.selectedId(), "forward"); });
        act("#pdBack", function () { if (state.selected()) state.reorder(state.selectedId(), "back"); });

        // ------- the properties panel
        let editing = false;

        props.addEventListener("focusin", function () { if (!editing && state.selected()) { state.checkpoint(); editing = true; } });
        props.addEventListener("focusout", function () { editing = false; });

        props.addEventListener("input", function (event) {

            const e = state.selected();
            const t = event.target;

            if (!e) return;

            if (t.id === "pdText") state.update(e.id, { text: t.value });
            else if (t.id === "pdSize") state.update(e.id, { fontSize: clamp(Number(t.value) || 16, 8, 96) });
            else if (t.id === "pdWeight") state.update(e.id, { fontWeight: Number(t.value) });
            else if (t.id === "pdAlign") state.update(e.id, { align: t.value });
            else if (t.id === "pdColor") state.update(e.id, { color: t.value });
            else if (t.id === "pdBg") state.update(e.id, { bg: t.value });
            else if (t.id === "pdNoFill") state.update(e.id, { bg: t.checked ? "" : (container.querySelector("#pdBg").value || "#ffffff") });
            else if (t.id === "pdRadius") state.update(e.id, { radius: Number(t.value) });
            else return;

            const node = canvas.querySelector('[data-id="' + e.id + '"]');
            if (node) {
                node.setAttribute("style", elementStyle(state.get(e.id)));
                const text = (e.type === "image" ? "▦ " : "") + state.get(e.id).text;
                const first = node.firstChild && node.firstChild.nodeType === 3 ? node.firstChild : null;
                if (first) first.nodeValue = text;
                else node.insertBefore(document.createTextNode(text), node.firstChild);
            }

            changed();
        });

        // ------- copy lines go into the selected element
        container.addEventListener("click", function (event) {
            const chip = event.target.closest && event.target.closest(".pd-chip");
            if (!chip) return;
            const e = state.selected();
            if (!e || e.type === "divider" || e.type === "section") return;
            state.checkpoint();
            state.update(e.id, { text: chip.dataset.copy });
            draw(); changed();
        });

        window.addEventListener("resize", fit);

        api.state = state;
        api.container = container;

        fit();
        draw();
    }

    const api = { html: html, mount: mount, renderStatic: renderStatic, createState: createState, parseLayout: parseLayout, TYPES: TYPES, GRID: GRID, CANVAS_W: CANVAS_W, CANVAS_H: CANVAS_H, MIN_ELEMENTS_TO_SUBMIT: MIN_ELEMENTS_TO_SUBMIT };

    api.collect = function (question) {
        const state = api.state;
        return { question_id: question.id, selected_answer: state && state.count() ? state.toJSON() : null };
    };

    api.isAnswered = function () {
        return !!(api.state && api.state.count() >= MIN_ELEMENTS_TO_SUBMIT);
    };

    return api;
});
