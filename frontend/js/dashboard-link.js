/* Every "Back to Dashboard" button goes to the candidate's own assessment dashboard (assessment.html) for the
   application being taken, never to the public index page. */
(function () {
    function applicationId() {
        const fromUrl = new URLSearchParams(window.location.search).get("application_id");
        return fromUrl && /^\d+$/.test(fromUrl) ? fromUrl : null;
    }

    function fix() {
        const id = applicationId();
        const href = "assessment.html" + (id ? "?application_id=" + encodeURIComponent(id) : "");
        document.querySelectorAll("a[data-dashboard-link]").forEach(function (a) { a.setAttribute("href", href); });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fix); else fix();
})();
