"""
A candidate whose first submit went through but who then sees "already submitted" must not be left stuck on the editor: the page shows the saved
result and offers the next step. (The first submit can succeed while its reply never reaches the page: a slow grading run, a throttled tab,
a second click.) The same guard exists on the role test and the practical-stage pages.
"""

import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_the_coding_page_opens_the_saved_result_when_the_server_says_it_was_already_submitted():
    js = _read("js", "programming-v3.js")
    branch = js[js.index("if (response.status === 409) {"):]
    branch = branch[:branch.index("if (response.status === 410)")]

    assert re.search(r"already\.\*submitted", branch)
    assert "await loadAssessmentState(true)" in branch                          # the same loader that shows the result after a reload
    assert branch.index("already") < branch.index("language")                   # checked before the generic "show the message and stop"


def test_the_coding_submit_button_is_not_switched_back_on_after_the_assessment_is_locked():
    js = _read("js", "programming-v3.js")
    assert re.search(r"finally \{\s*submitInFlight = false;\s*if \(!assessmentLocked\) \{\s*submitButton\.disabled = false;", js)


def test_the_coding_result_page_tells_the_candidate_to_continue():
    html = _read("programming-v3.html")
    result = html[html.index('id="pv3ResultSection"'):]
    result = result[:result.index("</div>\n        </div>") if "</div>\n        </div>" in result else 900]
    assert "Continue to the next step" in result and "Back to Dashboard" not in result


def test_the_role_test_and_practical_pages_reload_to_the_saved_result_on_an_already_submitted_reply():
    for page in ("aptitude-test.js", "sjt-test.js", "tda-test.js"):
        js = _read("js", page)
        guard = js[js.index("response.status === 409 &&"):][:260]
        assert "already.*(submitted|completed)" in guard and "window.location.reload()" in guard, page
