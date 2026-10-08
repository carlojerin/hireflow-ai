/* The intro of the stage-3 page describes the test the candidate is about to take. The page is shared by the situational-judgment test and the
   practical (work-sample) tests, so for a practical test the generic multiple-choice description is replaced with the right one. */
(function () {
    "use strict";

    var API = "http://127.0.0.1:8000";

    var TEXT = {
        written:
            "This is a practical task. You will read two short work situations (for example a customer email or a brief) and write your response to each, " +
            "the way you would on the job. Your writing is checked for accuracy, clarity, tone and how well it solves the situation, and the hiring team " +
            "reads your answers too. You need <strong>60% or higher</strong> to pass. You have <strong>25 minutes</strong> once you start, and can only " +
            "submit once.",
        data:
            "This is a practical task. You will see a table of data and answer five questions by working from it. The page gives you tools like a " +
            "spreadsheet: sort and filter the rows, click cells to see their count, sum and average, and use the calculator. Each answer is checked " +
            "against the table. You need <strong>60% or higher</strong> to pass. You have <strong>25 minutes</strong> once you start, and can only " +
            "submit once.",
    };

    TEXT.roleplay =
        "This is a practical call simulation. You will take a phone call from an AI customer, prospect or client and handle it as you would on the job. " +
        "You will read a short brief of your role and the company rules first. Speak clearly and use headphones if you can. The call lasts a few minutes. " +
        "Your call is transcribed and checked for accuracy, empathy and process, and the hiring team reads the transcript too. You need " +
        "<strong>60% or higher</strong> to pass. You have <strong>25 minutes</strong> once you start, and can only submit once.";

    TEXT.lab =
        "This is a hands-on technical lab. You will be given a real-looking artifact, such as a server log, a configuration file, a network plan or a piece of code, " +
        "and five questions about it. Search the text, mark lines and use the calculator to work out each answer; each answer is checked exactly. " +
        "You need <strong>60% or higher</strong> to pass. You have <strong>30 minutes</strong> once you start, and can only submit once.";

    TEXT.design =
        "This is a practical web design task. You will get a short brief for a web page and build it on a canvas by dragging components onto it: a navigation bar, " +
        "headings, text, buttons, images, cards and a footer. Move and resize them, change colours and text, and make the page clear and good to look at. " +
        "Your page is checked for what the brief asks for, readable colours, a clear hierarchy and alignment, and judged for layout and fit to the brief. " +
        "You need <strong>60% or higher</strong> to pass. You have <strong>40 minutes</strong> once you start, and can only submit once.";

    function applicationId() {
        var value = new URLSearchParams(window.location.search).get("application_id");
        return value && /^\d+$/.test(value) ? value : null;
    }

    function run() {
        var id = applicationId();
        var target = document.getElementById("introDescription");

        if (!id || !target) return;

        fetch(API + "/api/assessment/sessions/" + id + "/titles", { credentials: "include" })
            .then(function (response) { return response.ok ? response.json() : null; })
            .then(function (titles) {
                var stage = titles && titles.stage3;
                if (stage && stage.kind === "practical" && TEXT[stage.environment]) target.innerHTML = TEXT[stage.environment];
            })
            .catch(function () { /* the standard description stays */ });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
    else run();
})();
