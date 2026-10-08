/* Shows each test under the name the AI planner gave it for THIS job (for example "Language & Grammar Assessment" instead of
   "Aptitude Test"). The pages are shared by every job, so this replaces the default names in the page text, including text the page's
   own scripts add later, once the names have been fetched. Does nothing when the job uses the standard tests. */
(function () {
    "use strict";

    var API = "http://127.0.0.1:8000";
    var DEFAULTS = { aptitude: "Aptitude Test", situational_judgment: "Situational Judgment Test" };
    var replacements = [];
    var observer = null;
    var queued = false;

    function applicationId() {
        var value = new URLSearchParams(window.location.search).get("application_id");
        return value && /^\d+$/.test(value) ? value : null;
    }

    // Replaces the default name with the planned one, leaving text that ALREADY holds the planned name untouched: a planned name can
    // contain the default ("Copywriting Situational Judgment Test"), and swapping it again every time the page changes would grow it forever.
    function swap(text, pair) {
        if (text.indexOf(pair[0]) === -1) return text;
        return text.split(pair[1]).map(function (part) { return part.split(pair[0]).join(pair[1]); }).join(pair[1]);
    }

    function replaceIn(root) {
        if (!replacements.length) return;
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
        var node;
        while ((node = walker.nextNode())) {
            var parent = node.parentNode && node.parentNode.nodeName;
            if (parent === "SCRIPT" || parent === "STYLE") continue;
            var text = node.nodeValue, next = text;
            replacements.forEach(function (pair) { next = swap(next, pair); });
            if (next !== text) node.nodeValue = next;
        }
        replacements.forEach(function (pair) {
            var title = swap(document.title, pair);
            if (title !== document.title) document.title = title;
        });
    }

    function schedule() {
        if (queued) return;
        queued = true;
        window.setTimeout(function () { queued = false; replaceIn(document.body); }, 30);
    }

    function start(titles) {
        Object.keys(DEFAULTS).forEach(function (key) {
            var planned = titles && titles[key];
            if (planned && planned !== DEFAULTS[key]) replacements.push([DEFAULTS[key], planned]);
        });
        if (!replacements.length) return;
        window.AssessmentTitles = titles;
        replaceIn(document.body);
        observer = new MutationObserver(schedule);
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    }

    function load() {
        var id = applicationId();
        if (!id) return;
        fetch(API + "/api/assessment/sessions/" + id + "/titles", { credentials: "include" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (titles) { if (titles) start(titles); })
            .catch(function () { /* the standard names simply stay */ });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load); else load();
})();
