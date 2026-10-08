import html as html_lib
import json
import logging
import random
import uuid
from concurrent.futures import ThreadPoolExecutor

from backend.app.schemas.programming_question_v2 import ProgrammingMainQuestionV2
from backend.app.services.assessment.html_runner import HtmlRunError, run_suite
from backend.app.services.assessment.programming_question_builders import build_question

logger = logging.getLogger("jobpilot.assessment.frontend")

# ============================================================
# JOBPILOT AI - WEB DEVELOPMENT (HTML / CSS / JAVASCRIPT) QUESTIONS
#
# For frontend roles: the candidate builds a small page; a real headless browser loads it
# and the checks in html_runner.py (structure, computed CSS, click/typing behaviour) decide
# the score.
#
#   * Gemini writes the task, a reference page and the checks.
#   * The SERVER runs the checks on the reference page (they must all pass) and on the empty
#     starter (most must fail): a check that cannot be met, or is met by doing nothing, is
#     dropped, and a question left with too few checks is replaced.
#   * Without Gemini, parametric templates build the same kind of task from random content
#     (names, colours, counts, steps), so every candidate gets a different set.
# ============================================================

NAMES = ["Asha Rao", "Kabir Mehta", "Meera Nair", "Rohan Iyer", "Diya Sharma", "Vikram Das", "Nisha Pillai", "Arjun Menon"]
ROLES = ["Frontend Developer", "UI Designer", "QA Engineer", "Product Analyst", "Backend Developer", "Data Analyst"]
SKILLS = ["HTML", "CSS", "JavaScript", "React", "Git", "Accessibility", "Flexbox", "REST APIs", "Testing", "Figma", "SQL", "Python"]
COLORS = {
    "#1e3a8a": "rgb(30, 58, 138)", "#065f46": "rgb(6, 95, 70)", "#7c2d12": "rgb(124, 45, 18)",
    "#4c1d95": "rgb(76, 29, 149)", "#0f766e": "rgb(15, 118, 110)", "#9f1239": "rgb(159, 18, 57)",
    "#1f2937": "rgb(31, 41, 55)", "#b45309": "rgb(180, 83, 9)",
}
MENU_WORDS = ["Home", "Products", "Pricing", "Blog", "Contact", "About", "Careers", "Docs", "Support", "Team"]
TASKS = ["Buy milk", "Pay rent", "Call Asha", "Read chapter 3", "Book tickets", "Fix bug 42", "Water plants", "Email report"]

PAGE = "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n{body}\n{script}</body>\n</html>\n"

STARTER = "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<title>Task</title>\n<style>\n/* your CSS here */\n</style>\n</head>\n<body>\n<!-- your HTML here -->\n<script>\n// your JavaScript here (if the task needs it)\n</script>\n</body>\n</html>\n"


def _page(title, css, body, script=""):
    return PAGE.format(title=title, css=css.strip(), body=body.strip(), script=(f"<script>\n{script.strip()}\n</script>\n" if script else ""))


def _e(text) -> str:
    return html_lib.escape(str(text))


def _case(case_id, description, *steps):
    return {"id": case_id, "description": description, "steps": list(steps)}


# ------------------------------------------------------------
# parametric templates: each returns a dict
#   title, description, requirements, cases (4; the first is the public example), reference, difficulty, skills
# ------------------------------------------------------------

def _t_profile_card(rng):
    name, role = rng.choice(NAMES), rng.choice(ROLES)
    skills = rng.sample(SKILLS, rng.choice([3, 4]))
    color = rng.choice(list(COLORS))
    radius, width = rng.choice([8, 12, 16]), rng.choice([300, 320, 360])
    reqs = [
        f"An <article class=\"card\"> containing <h2 class=\"name\">{name}</h2> and <p class=\"role\">{role}</p>.",
        "An <img class=\"avatar\"> with a meaningful (non-empty) alt attribute.",
        f"A <ul class=\"skills\"> with one <li> per skill, in this order: {', '.join(skills)}.",
        f"CSS: the card has background-color {color}, border-radius {radius}px, max-width {width}px and padding 16px.",
    ]
    body = (f'<article class="card"><img class="avatar" alt="Portrait of {_e(name)}" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'
            f'<h2 class="name">{_e(name)}</h2><p class="role">{_e(role)}</p><ul class="skills">{"".join(f"<li>{_e(s)}</li>" for s in skills)}</ul></article>')
    css = f".card{{background-color:{color};color:#fff;border-radius:{radius}px;max-width:{width}px;padding:16px}}"
    cases = [
        _case("tc1", "Card with name and role", {"expect": "tag", "sel": ".card", "is": "article"}, {"expect": "text", "sel": "h2.name", "equals": name},
              {"expect": "text", "sel": "p.role", "equals": role}),
        _case("tc2", "Avatar and skills", {"expect": "attr", "sel": "img.avatar", "name": "alt", "nonempty": True},
              {"expect": "count", "sel": "ul.skills li", "n": len(skills)}, {"expect": "texts", "sel": "ul.skills li", "equals": skills}),
        _case("tc3", "Card colours and shape", {"expect": "style", "sel": ".card", "prop": "background-color", "equals": COLORS[color]},
              {"expect": "style", "sel": ".card", "prop": "border-top-left-radius", "equals": f"{radius}px"}),
        _case("tc4", "Card size", {"expect": "style", "sel": ".card", "prop": "max-width", "equals": f"{width}px"},
              {"expect": "style", "sel": ".card", "prop": "padding-top", "equals": "16px"}),
    ]
    return dict(title="Profile card", description="Build a profile card page with semantic HTML and CSS.", requirements=reqs, cases=cases,
                reference=_page("Card", css, body), difficulty="basic", skills=["semantic HTML", "CSS box model", "lists"])


def _t_nav_flex(rng):
    links = rng.sample(MENU_WORDS, rng.choice([4, 5]))
    color = rng.choice(list(COLORS))
    gap = rng.choice([12, 16, 24])
    reqs = [
        f"A <nav> with one <a> per item, in this order: {', '.join(links)}. Each link's href is '#' followed by the lowercase text (for example #home).",
        f"CSS: the nav is a flex row (display: flex), has background-color {color} and a {gap}px gap between links.",
        "The links are white (color: rgb(255, 255, 255)) with no underline (text-decoration-line: none).",
    ]
    body = "<nav>" + "".join(f'<a href="#{w.lower()}">{w}</a>' for w in links) + "</nav>"
    css = f"nav{{display:flex;gap:{gap}px;background-color:{color};padding:12px}}nav a{{color:#fff;text-decoration:none}}"
    cases = [
        _case("tc1", "Navigation links", {"expect": "exists", "sel": "nav"}, {"expect": "count", "sel": "nav a", "n": len(links)},
              {"expect": "texts", "sel": "nav a", "equals": links}),
        _case("tc2", "Link targets", {"expect": "attr", "sel": "nav a", "index": 0, "name": "href", "equals": "#" + links[0].lower()},
              {"expect": "attr", "sel": "nav a", "index": len(links) - 1, "name": "href", "equals": "#" + links[-1].lower()}),
        _case("tc3", "Flex layout", {"expect": "style", "sel": "nav", "prop": "display", "equals": "flex"},
              {"expect": "style", "sel": "nav", "prop": "background-color", "equals": COLORS[color]},
              {"expect": "style", "sel": "nav", "prop": "column-gap", "equals": f"{gap}px"}),
        _case("tc4", "Link styling", {"expect": "style", "sel": "nav a", "prop": "color", "equals": "rgb(255, 255, 255)"},
              {"expect": "style", "sel": "nav a", "prop": "text-decoration-line", "equals": "none"}),
    ]
    return dict(title="Flexbox navigation bar", description="Build a horizontal navigation bar with Flexbox.", requirements=reqs, cases=cases,
                reference=_page("Nav", css, body), difficulty="basic", skills=["flexbox", "links", "CSS colours"])


def _t_contact_form(rng):
    button = rng.choice(["Send", "Submit", "Contact us"])
    color = rng.choice(list(COLORS))
    reqs = [
        "A <form> with three labelled fields: a text input id='name' (required), an email input id='email' (type=email, required) and a textarea id='message'.",
        "Every field has a <label> whose 'for' attribute matches the field's id.",
        f"A submit button (type=submit) with the text '{button}', background-color {color}.",
    ]
    body = ('<form><label for="name">Name</label><input id="name" type="text" required>'
            '<label for="email">Email</label><input id="email" type="email" required>'
            '<label for="message">Message</label><textarea id="message"></textarea>'
            f'<button type="submit">{button}</button></form>')
    css = f"form{{display:grid;gap:8px;max-width:360px}}button{{background-color:{color};color:#fff;border:0;padding:8px}}"
    cases = [
        _case("tc1", "Form and fields", {"expect": "exists", "sel": "form"}, {"expect": "exists", "sel": "input#name"}, {"expect": "exists", "sel": "textarea#message"}),
        _case("tc2", "Email field", {"expect": "attr", "sel": "input#email", "name": "type", "equals": "email"},
              {"expect": "exists", "sel": "input#email[required]"}),
        _case("tc3", "Labels", {"expect": "exists", "sel": "label[for='name']"}, {"expect": "exists", "sel": "label[for='email']"}, {"expect": "exists", "sel": "label[for='message']"}),
        _case("tc4", "Submit button", {"expect": "text", "sel": "button[type='submit']", "equals": button},
              {"expect": "style", "sel": "button[type='submit']", "prop": "background-color", "equals": COLORS[color]}),
    ]
    return dict(title="Accessible contact form", description="Build a contact form with proper labels and a styled submit button.", requirements=reqs,
                cases=cases, reference=_page("Form", css, body), difficulty="basic", skills=["forms", "accessibility", "labels"])


def _t_data_table(rng):
    columns = rng.choice([["Name", "Team", "Score"], ["Item", "Qty", "Price"], ["City", "Users", "Growth"]])
    n = rng.choice([3, 4, 5])
    rows = [[rng.choice(MENU_WORDS), str(rng.randint(2, 9)), str(rng.randint(10, 99))] for _ in range(n)]
    reqs = [
        f"A <table> with a <thead> row of <th> cells: {', '.join(columns)}.",
        f"A <tbody> with exactly {n} rows, in this order: " + "; ".join(", ".join(r) for r in rows) + ".",
        "CSS: table border-collapse is collapse and the header cells have font-weight 700.",
    ]
    body = ("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in columns) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")
    css = "table{border-collapse:collapse}th,td{border:1px solid #999;padding:6px}th{font-weight:700}"
    cases = [
        _case("tc1", "Header cells", {"expect": "count", "sel": "thead th", "n": 3}, {"expect": "texts", "sel": "thead th", "equals": columns}),
        _case("tc2", "Body rows", {"expect": "count", "sel": "tbody tr", "n": n}, {"expect": "texts", "sel": "tbody tr:first-child td", "equals": rows[0]}),
        _case("tc3", "Last row", {"expect": "texts", "sel": "tbody tr:last-child td", "equals": rows[-1]}),
        _case("tc4", "Table styling", {"expect": "style", "sel": "table", "prop": "border-collapse", "equals": "collapse"},
              {"expect": "style", "sel": "th", "prop": "font-weight", "equals": "700"}),
    ]
    return dict(title="Data table", description="Build a data table with a header row and styled cells.", requirements=reqs, cases=cases,
                reference=_page("Table", css, body), difficulty="basic", skills=["tables", "semantic HTML", "CSS"])


def _t_grid_gallery(rng):
    count, gap, cols = rng.choice([6, 8, 9]), rng.choice([12, 16, 20]), rng.choice([3, 4])
    color = rng.choice(list(COLORS))
    reqs = [
        f"A <div class=\"gallery\"> holding exactly {count} <div class=\"item\"> tiles, numbered 1 to {count} (the tile text is its number).",
        f"CSS: .gallery uses CSS Grid (display: grid) with {cols} equal columns and a {gap}px gap between rows.",
        f"Every .item has background-color {color}, white text and padding 12px.",
    ]
    body = '<div class="gallery">' + "".join(f'<div class="item">{i}</div>' for i in range(1, count + 1)) + "</div>"
    css = (f".gallery{{display:grid;grid-template-columns:repeat({cols},1fr);gap:{gap}px}}"
           f".item{{background-color:{color};color:#fff;padding:12px}}")
    cases = [
        _case("tc1", "Tiles", {"expect": "exists", "sel": ".gallery"}, {"expect": "count", "sel": ".gallery .item", "n": count}),
        _case("tc2", "Tile numbers", {"expect": "text", "sel": ".item", "index": 0, "equals": "1"}, {"expect": "text", "sel": ".item", "index": count - 1, "equals": str(count)}),
        _case("tc3", "Grid layout", {"expect": "style", "sel": ".gallery", "prop": "display", "equals": "grid"},
              {"expect": "style", "sel": ".gallery", "prop": "row-gap", "equals": f"{gap}px"}),
        _case("tc4", "Tile styling", {"expect": "style", "sel": ".item", "prop": "background-color", "equals": COLORS[color]},
              {"expect": "style", "sel": ".item", "prop": "padding-top", "equals": "12px"}),
    ]
    return dict(title="CSS Grid gallery", description="Lay out a gallery of tiles with CSS Grid.", requirements=reqs, cases=cases,
                reference=_page("Grid", css, body), difficulty="intermediate", skills=["CSS Grid", "layout", "spacing"])


def _t_counter(rng):
    start, step = rng.randint(0, 9), rng.choice([2, 3, 5])
    reqs = [
        f"A <span id=\"count\"> that starts at {start}.",
        f"A button #inc that adds {step} to the count, a button #dec that subtracts {step}, and a button #reset that returns the count to {start}.",
        "The count is plain text inside #count (just the number).",
    ]
    body = f'<span id="count">{start}</span><button id="inc">+</button><button id="dec">-</button><button id="reset">Reset</button>'
    script = (f'var c={start};var el=document.getElementById("count");function show(){{el.textContent=String(c)}}'
              f'document.getElementById("inc").addEventListener("click",function(){{c+={step};show()}});'
              f'document.getElementById("dec").addEventListener("click",function(){{c-={step};show()}});'
              f'document.getElementById("reset").addEventListener("click",function(){{c={start};show()}});')
    cases = [
        _case("tc1", "Initial value", {"expect": "text", "sel": "#count", "equals": str(start)}, {"expect": "exists", "sel": "#inc"}),
        _case("tc2", "Increase", {"do": "click", "sel": "#inc"}, {"do": "click", "sel": "#inc"}, {"expect": "text", "sel": "#count", "equals": str(start + 2 * step)}),
        _case("tc3", "Decrease", {"do": "click", "sel": "#dec"}, {"expect": "text", "sel": "#count", "equals": str(start + step)}),
        _case("tc4", "Reset", {"do": "click", "sel": "#inc"}, {"do": "click", "sel": "#reset"}, {"expect": "text", "sel": "#count", "equals": str(start)}),
    ]
    return dict(title="Interactive counter", description="Build a counter with JavaScript event handlers.", requirements=reqs, cases=cases,
                reference=_page("Counter", "button{margin:4px}", body, script), difficulty="intermediate", skills=["DOM events", "state", "JavaScript"])


def _t_todo(rng):
    first, second = rng.sample(TASKS, 2)
    reqs = [
        "A text input #task-input, a button #add-btn and an empty list <ul id=\"task-list\">.",
        "Clicking #add-btn adds an <li> with the typed text to #task-list and clears the input.",
        "Empty (or blank) text is ignored: nothing is added.",
        "A <span id=\"total\"> always shows how many tasks are in the list (just the number, 0 at the start).",
    ]
    body = '<input id="task-input" type="text"><button id="add-btn">Add</button><ul id="task-list"></ul><span id="total">0</span>'
    script = ('var input=document.getElementById("task-input"),list=document.getElementById("task-list"),total=document.getElementById("total");'
              'document.getElementById("add-btn").addEventListener("click",function(){var t=input.value.trim();if(!t){return}'
              'var li=document.createElement("li");li.textContent=t;list.appendChild(li);input.value="";total.textContent=String(list.children.length)});')
    cases = [
        _case("tc1", "Add a task", {"expect": "text", "sel": "#total", "equals": "0"}, {"do": "input", "sel": "#task-input", "value": first},
              {"do": "click", "sel": "#add-btn"}, {"expect": "texts", "sel": "#task-list li", "equals": [first]}),
        _case("tc2", "Input is cleared", {"expect": "value", "sel": "#task-input", "equals": ""}),
        _case("tc3", "Second task and total", {"do": "input", "sel": "#task-input", "value": second}, {"do": "click", "sel": "#add-btn"},
              {"expect": "texts", "sel": "#task-list li", "equals": [first, second]}, {"expect": "text", "sel": "#total", "equals": "2"}),
        _case("tc4", "Blank text is ignored", {"do": "input", "sel": "#task-input", "value": "   "}, {"do": "click", "sel": "#add-btn"},
              {"expect": "count", "sel": "#task-list li", "n": 2}, {"expect": "text", "sel": "#total", "equals": "2"}),
    ]
    return dict(title="To-do list", description="Build a small to-do list that updates the page with JavaScript.", requirements=reqs, cases=cases,
                reference=_page("Todo", "li{margin:2px}", body, script), difficulty="intermediate", skills=["DOM manipulation", "input handling", "state"])


def _t_toggle_panel(rng):
    show, hide = rng.choice([("Show details", "Hide details"), ("Open", "Close"), ("Expand", "Collapse")])
    reqs = [
        f"A button #toggle with the text '{show}' and a <div id=\"panel\"> with some text that is hidden at first.",
        f"Clicking the button shows the panel and changes the button text to '{hide}'; clicking again hides it and restores '{show}'.",
        "Hidden means display: none.",
    ]
    body = f'<button id="toggle">{show}</button><div id="panel" style="display:none">More information about the product.</div>'
    script = (f'var p=document.getElementById("panel"),b=document.getElementById("toggle");b.addEventListener("click",function(){{'
              f'var open=p.style.display==="none";p.style.display=open?"block":"none";b.textContent=open?"{hide}":"{show}"}});')
    cases = [
        _case("tc1", "Starts hidden", {"expect": "text", "sel": "#toggle", "equals": show}, {"expect": "visible", "sel": "#panel", "value": False}),
        _case("tc2", "Opens", {"do": "click", "sel": "#toggle"}, {"expect": "visible", "sel": "#panel", "value": True}, {"expect": "text", "sel": "#toggle", "equals": hide}),
        _case("tc3", "Closes again", {"do": "click", "sel": "#toggle"}, {"expect": "visible", "sel": "#panel", "value": False}, {"expect": "text", "sel": "#toggle", "equals": show}),
        _case("tc4", "Works repeatedly", {"do": "click", "sel": "#toggle"}, {"do": "click", "sel": "#toggle"}, {"do": "click", "sel": "#toggle"},
              {"expect": "visible", "sel": "#panel", "value": True}),
    ]
    return dict(title="Show / hide panel", description="Toggle a panel with a button and keep the button label in sync.", requirements=reqs, cases=cases,
                reference=_page("Toggle", "", body, script), difficulty="intermediate", skills=["DOM events", "visibility", "state"])


def _t_tabs(rng):
    labels = rng.sample(MENU_WORDS, 3)
    reqs = [
        f"Three buttons with class 'tab' labelled {', '.join(labels)} and three <div class=\"panel\"> elements, one per tab.",
        "Only the panel of the active tab is visible; the first tab is active at the start.",
        "Clicking a tab shows its panel, hides the others and moves the class 'active' to the clicked tab (only one tab has it).",
    ]
    body = ("<div>" + "".join(f'<button class="tab{" active" if i == 0 else ""}">{w}</button>' for i, w in enumerate(labels)) + "</div>"
            + "".join(f'<div class="panel" style="display:{"block" if i == 0 else "none"}">{w} content</div>' for i, w in enumerate(labels)))
    script = ('var tabs=document.querySelectorAll(".tab"),panels=document.querySelectorAll(".panel");tabs.forEach(function(t,i){'
              't.addEventListener("click",function(){tabs.forEach(function(x){x.classList.remove("active")});t.classList.add("active");'
              'panels.forEach(function(p,j){p.style.display=j===i?"block":"none"})})});')
    cases = [
        _case("tc1", "Tabs and panels", {"expect": "count", "sel": ".tab", "n": 3}, {"expect": "count", "sel": ".panel", "n": 3},
              {"expect": "visible", "sel": ".panel", "index": 0, "value": True}, {"expect": "visible", "sel": ".panel", "index": 1, "value": False}),
        _case("tc2", "Switch to the second tab", {"do": "click", "sel": ".tab", "index": 1}, {"expect": "visible", "sel": ".panel", "index": 1, "value": True},
              {"expect": "visible", "sel": ".panel", "index": 0, "value": False}),
        _case("tc3", "Active class moves", {"expect": "count", "sel": ".tab.active", "n": 1}, {"expect": "attr", "sel": ".tab", "index": 1, "name": "class", "contains": "active"}),
        _case("tc4", "Switch to the third tab", {"do": "click", "sel": ".tab", "index": 2}, {"expect": "visible", "sel": ".panel", "index": 2, "value": True},
              {"expect": "visible", "sel": ".panel", "index": 1, "value": False}, {"expect": "count", "sel": ".tab.active", "n": 1}),
    ]
    return dict(title="Tabs", description="Build a tab switcher with JavaScript.", requirements=reqs, cases=cases,
                reference=_page("Tabs", "", body, script), difficulty="advanced", skills=["DOM events", "classList", "state"])


def _t_form_validation(rng):
    ok = rng.choice(["Thanks!", "Subscribed!", "Welcome aboard!"])
    reqs = [
        "An input #email, a button #submit and two <span> elements: #error and #success (both empty and hidden at first).",
        "Clicking #submit with an email that does not contain both '@' and '.' shows #error with the text 'Please enter a valid email' and keeps #success hidden.",
        f"With a valid email, #error is hidden and #success shows '{ok}'.",
        "Hidden means display: none.",
    ]
    body = ('<input id="email" type="text"><button id="submit">Subscribe</button>'
            '<span id="error" style="display:none"></span><span id="success" style="display:none"></span>')
    script = (f'var e=document.getElementById("email"),er=document.getElementById("error"),su=document.getElementById("success");'
              f'document.getElementById("submit").addEventListener("click",function(){{var v=e.value.trim();var good=v.indexOf("@")>0&&v.indexOf(".")>v.indexOf("@");'
              f'er.style.display=good?"none":"inline";su.style.display=good?"inline":"none";er.textContent=good?"":"Please enter a valid email";su.textContent=good?"{ok}":""}});')
    cases = [
        _case("tc1", "Hidden at first", {"expect": "visible", "sel": "#error", "value": False}, {"expect": "visible", "sel": "#success", "value": False}),
        _case("tc2", "Invalid email", {"do": "input", "sel": "#email", "value": "not-an-email"}, {"do": "click", "sel": "#submit"},
              {"expect": "text", "sel": "#error", "equals": "Please enter a valid email"}, {"expect": "visible", "sel": "#success", "value": False}),
        _case("tc3", "Valid email", {"do": "input", "sel": "#email", "value": "asha@example.com"}, {"do": "click", "sel": "#submit"},
              {"expect": "text", "sel": "#success", "equals": ok}, {"expect": "visible", "sel": "#error", "value": False}),
        _case("tc4", "Invalid again", {"do": "input", "sel": "#email", "value": "asha@example"}, {"do": "click", "sel": "#submit"},
              {"expect": "visible", "sel": "#error", "value": True}, {"expect": "visible", "sel": "#success", "value": False}),
    ]
    return dict(title="Email validation", description="Validate an email field in JavaScript and show feedback.", requirements=reqs, cases=cases,
                reference=_page("Validate", "", body, script), difficulty="advanced", skills=["validation", "DOM events", "conditional UI"])


def _t_theme_switch(rng):
    light, dark = rng.sample(list(COLORS), 2)
    reqs = [
        f"A button #theme and CSS so that the page body has background-color {light} at first.",
        f"Clicking the button adds the class 'dark' to <body>, which changes the body background-color to {dark}; clicking again removes it and restores {light}.",
    ]
    body = '<button id="theme">Switch theme</button><p>Hello</p>'
    css = f"body{{background-color:{light};color:#fff}}body.dark{{background-color:{dark}}}"
    script = 'document.getElementById("theme").addEventListener("click",function(){document.body.classList.toggle("dark")});'
    cases = [
        _case("tc1", "Light theme", {"expect": "exists", "sel": "#theme"}, {"expect": "style", "sel": "body", "prop": "background-color", "equals": COLORS[light]}),
        _case("tc2", "Dark theme", {"do": "click", "sel": "#theme"}, {"expect": "attr", "sel": "body", "name": "class", "contains": "dark"},
              {"expect": "style", "sel": "body", "prop": "background-color", "equals": COLORS[dark]}),
        _case("tc3", "Back to light", {"do": "click", "sel": "#theme"}, {"expect": "style", "sel": "body", "prop": "background-color", "equals": COLORS[light]}),
        _case("tc4", "Toggles again", {"do": "click", "sel": "#theme"}, {"expect": "style", "sel": "body", "prop": "background-color", "equals": COLORS[dark]}),
    ]
    return dict(title="Theme switcher", description="Switch between two colour themes with a class toggle.", requirements=reqs, cases=cases,
                reference=_page("Theme", css, body, script), difficulty="intermediate", skills=["classList", "CSS specificity", "DOM events"])


TEMPLATES = {
    "basic": [_t_profile_card, _t_nav_flex, _t_contact_form, _t_data_table],
    "intermediate": [_t_grid_gallery, _t_counter, _t_todo, _t_toggle_panel, _t_theme_switch],
    "advanced": [_t_tabs, _t_form_validation],
}

DIFFICULTY_LABEL = {"basic": "easy", "intermediate": "medium", "advanced": "hard"}

BAND_DIFFICULTIES = {
    "fresher": ["basic", "basic", "basic", "intermediate", "intermediate"],
    "junior": ["basic", "intermediate", "intermediate", "intermediate", "advanced"],
    "senior": ["intermediate", "intermediate", "advanced", "advanced", "advanced"],
}


def difficulty_slots(band: str, count: int) -> list[str]:
    return list(BAND_DIFFICULTIES.get(band, BAND_DIFFICULTIES["junior"]))[:count]


def _to_question(spec: dict) -> ProgrammingMainQuestionV2:
    """Turns a template/Gemini spec into a question. The public case gets its own suite; the hidden cases share one."""

    cases = spec["cases"]
    public_suite = [{"id": cases[0]["id"], "steps": cases[0]["steps"]}]
    hidden_suite = [{"id": c["id"], "steps": c["steps"]} for c in cases[1:]]

    test_cases = []

    for index, case in enumerate(cases):
        suite = public_suite if index == 0 else hidden_suite
        test_cases.append({
            "stdin": json.dumps({"case": case["id"], "suite": suite}),
            "expected_stdout": "PASS",
            "is_hidden": index != 0,
            "description": case.get("description"),
        })

    requirement_text = "\n".join(f"- {r}" for r in spec["requirements"])

    return build_question(
        title=spec["title"],
        description=f"{spec['description']}\n\nRequirements:\n{requirement_text}\n\nWrite ONE HTML file with inline <style> and <script>. It is opened in a real browser and checked.",
        input_specification="Your single HTML file (HTML, CSS and JavaScript together).",
        output_specification="The page is loaded in a browser; its structure, styles and behaviour are checked against the requirements.",
        difficulty=DIFFICULTY_LABEL.get(spec["difficulty"], "medium"),
        allowed_languages=["html"],
        cases=test_cases,
        examples=[],
        requirements=spec["requirements"],
        skills_assessed=spec.get("skills", ["HTML", "CSS"]),
        technology="HTML/CSS/JavaScript",
        execution_mode="frontend_coding",
        notes="No external files or network: everything must be inside the one HTML file.",
    )


def parametric_frontend_question(difficulty: str, rng: random.Random, used: set | None = None) -> tuple[ProgrammingMainQuestionV2, str]:
    """Returns (question, reference_html)."""

    used = used if used is not None else set()

    for _attempt in range(30):

        template = rng.choice(TEMPLATES[difficulty])

        if template in used and _attempt < 20:
            continue

        spec = template(rng)
        used.add(template)
        return _to_question(spec), spec["reference"]

    raise RuntimeError("could not build a frontend question")


# ------------------------------------------------------------
# Gemini path (validated by really running the checks)
# ------------------------------------------------------------

def _gemini_prompt(job, band: str, slots: list[str], candidate_skills: list[str], variation: str) -> str:

    slot_lines = "\n".join(f"{i}. difficulty={d}" for i, d in enumerate(slots, start=1))

    return f"""
You are a senior frontend engineer writing a practical web-development test (HTML, CSS, vanilla JavaScript) for a real hiring pipeline.

JOB TITLE: {getattr(job, 'title', '')}
JOB DESCRIPTION: {getattr(job, 'description', '') or 'Not specified'}
REQUIRED SKILLS: {getattr(job, 'required_skills', '') or 'Not specified'}
CANDIDATE EXPERIENCE BAND: {band}
CANDIDATE SKILLS FROM THE RESUME: {', '.join(candidate_skills) or 'Not specified'}
VARIATION CODE: {variation} (use it to choose fresh content, names, colours and numbers so this test differs from every other candidate's)

Write EXACTLY {len(slots)} tasks, one per slot, each a small realistic UI component the candidate builds as ONE HTML file with inline <style> and <script>
(no libraries, no network). Mix layout/CSS tasks and interactive JavaScript tasks according to the difficulty and the job.
SLOTS:
{slot_lines}

For each task return:
- "title", "description" (2-3 sentences), "requirements" (4-6 precise, checkable bullet strings that name the exact ids/classes/text/colours to use)
- "difficulty": basic | intermediate | advanced
- "reference_html": a COMPLETE correct solution page
- "cases": 4 checks. Case 1 is shown to the candidate. Each case: {{"id": "tc1", "description": "...", "steps": [ ... ]}}

Steps are objects of exactly these kinds:
  {{"do": "click", "sel": CSS_SELECTOR, "index": 0}}   {{"do": "input", "sel": CSS_SELECTOR, "value": "text"}}   {{"do": "wait", "ms": 100}}
  {{"expect": "exists", "sel": S, "min": 1}}  {{"expect": "count", "sel": S, "n": 3}}  {{"expect": "tag", "sel": S, "is": "nav"}}
  {{"expect": "text", "sel": S, "equals": "..."}} or "contains"   {{"expect": "texts", "sel": S, "equals": ["a", "b"]}}
  {{"expect": "attr", "sel": S, "name": "alt", "nonempty": true}} or "equals"/"contains"   {{"expect": "value", "sel": S, "equals": "..."}}
  {{"expect": "style", "sel": S, "prop": "display", "equals": "flex"}}   (computed style: colours as rgb(r, g, b), lengths in px)
  {{"expect": "visible", "sel": S, "value": true}}
Every check must pass on reference_html and be impossible for an empty page. Cases run in order on ONE page load, so later cases may depend on earlier clicks.
Return ONLY JSON: {{"questions": [ ... ]}}
""".strip()


def _validate_gemini_question(raw: dict) -> tuple[dict, str]:
    """Structural checks only; the reference/empty-page runs happen in a batch afterwards."""

    cases = raw["cases"]

    if not isinstance(cases, list) or not (3 <= len(cases) <= 5):
        raise ValueError("3-5 cases required")

    clean_cases = []

    for index, case in enumerate(cases, start=1):

        steps = case["steps"]

        if not isinstance(steps, list) or not steps or len(steps) > 12:
            raise ValueError("a case needs 1-12 steps")

        for step in steps:
            if not isinstance(step, dict) or not ("do" in step or "expect" in step) or "sel" not in step and step.get("do") != "wait":
                raise ValueError("bad step")

        clean_cases.append({"id": f"tc{index}", "description": str(case.get("description", ""))[:200], "steps": steps})

    reference = str(raw["reference_html"])

    if "<" not in reference:
        raise ValueError("reference page missing")

    spec = dict(
        title=str(raw["title"])[:120], description=str(raw["description"])[:700],
        requirements=[str(r) for r in raw["requirements"]][:8], cases=clean_cases,
        difficulty=str(raw.get("difficulty", "intermediate")).lower() if str(raw.get("difficulty", "")).lower() in DIFFICULTY_LABEL else "intermediate",
        skills=["HTML", "CSS", "JavaScript"],
    )

    return spec, reference


def _check_against_pages(spec: dict, reference: str) -> dict:
    """Runs the suite on the reference page and on the empty starter; keeps only meaningful checks."""

    suite = [{"id": c["id"], "steps": c["steps"]} for c in spec["cases"]]

    on_reference = run_suite(reference, suite)
    on_blank = run_suite(STARTER, suite)

    kept = [c for c in spec["cases"] if on_reference.get(c["id"]) == "PASS" and on_blank.get(c["id"], "PASS") != "PASS"]

    if len(kept) < 3:
        raise ValueError(f"only {len(kept)} usable checks")

    # the first kept case becomes the public example; ids are renumbered
    for index, case in enumerate(kept, start=1):
        case["id"] = f"tc{index}"

    spec = dict(spec)
    spec["cases"] = kept
    return spec


def generate_frontend_questions(
    count: int,
    job,
    band: str,
    candidate_skills: list[str],
    use_gemini: bool = True,
    rng: random.Random | None = None,
) -> tuple[list[ProgrammingMainQuestionV2], str]:

    rng = rng or random.Random()
    difficulties = difficulty_slots(band, count)
    questions: list[ProgrammingMainQuestionV2] = []
    from_gemini = 0

    if use_gemini:

        try:

            from backend.app.services.assessment.gemini_client import generate_json_content

            prompt = _gemini_prompt(job, band, difficulties, candidate_skills, uuid.uuid4().hex[:10])
            parsed = json.loads(generate_json_content(prompt, retries=2))

            prepared = []

            for raw in parsed.get("questions", [])[:count]:
                try:
                    prepared.append(_validate_gemini_question(raw))
                except Exception as exc:
                    logger.warning("A Gemini web question was rejected: %s", exc)

            def check(item):
                spec, reference = item
                try:
                    return _check_against_pages(spec, reference)
                except (HtmlRunError, ValueError, KeyError) as exc:
                    logger.warning("A Gemini web question failed its browser check: %s", exc)
                    return None

            with ThreadPoolExecutor(max_workers=4) as pool:
                for checked in pool.map(check, prepared):
                    if checked is not None:
                        questions.append(_to_question(checked))
                        from_gemini += 1

        except Exception as exc:
            logger.warning("Gemini web generation unavailable (%s); using parametric web questions.", exc)

    used: set = set()

    for slot in range(len(questions), count):
        question, _reference = parametric_frontend_question(difficulties[slot], rng, used)
        questions.append(question)

    return questions, ("gemini" if from_gemini == count else "fallback")
