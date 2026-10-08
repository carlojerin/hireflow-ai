"""
Bootstrap, Bootstrap Icons and LiveKit (role-play) are served from frontend/vendor, so the pages work on a network that blocks the public CDN.
The AI interview page is deliberately left exactly as it was (it keeps its CDN links).
"""

import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
FROZEN = {"ai-interview-v2.html"}


def _pages():
    return [p for p in sorted(glob.glob(os.path.join(FRONTEND, "*.html"))) if os.path.basename(p) not in FROZEN]


def test_the_vendored_files_exist_and_are_not_empty():
    for rel in ("bootstrap/bootstrap.min.css", "bootstrap/bootstrap.bundle.min.js", "bootstrap-icons/bootstrap-icons.min.css",
                "bootstrap-icons/fonts/bootstrap-icons.woff2", "bootstrap-icons/fonts/bootstrap-icons.woff", "livekit/livekit-client.umd.min.js"):
        path = os.path.join(FRONTEND, "vendor", rel)
        assert os.path.isfile(path) and os.path.getsize(path) > 50_000, rel


def test_the_icon_stylesheet_points_at_font_files_that_exist():
    css = open(os.path.join(FRONTEND, "vendor", "bootstrap-icons", "bootstrap-icons.min.css"), encoding="utf-8").read()
    for name in re.findall(r'url\("(fonts/[^"?]+)', css):
        assert os.path.isfile(os.path.join(FRONTEND, "vendor", "bootstrap-icons", name)), name


def test_no_page_except_the_interview_loads_bootstrap_or_livekit_from_the_internet():
    offenders = []

    for page in _pages():
        html = open(page, encoding="utf-8").read()
        if re.search(r"cdn\.jsdelivr\.net/npm/(bootstrap|bootstrap-icons|livekit-client)@", html):
            offenders.append(os.path.basename(page))

    assert offenders == []


def test_every_local_vendor_reference_in_a_page_resolves_to_a_file():
    for page in _pages():
        html = open(page, encoding="utf-8").read()
        for ref in re.findall(r'(?:href|src)="(vendor/[^"?]+)', html):
            assert os.path.isfile(os.path.join(FRONTEND, ref)), (os.path.basename(page), ref)


def test_the_interview_page_is_untouched_by_the_bundling():
    html = open(os.path.join(FRONTEND, "ai-interview-v2.html"), encoding="utf-8").read()
    assert "cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" in html and "livekit-client@2.22.3" in html
