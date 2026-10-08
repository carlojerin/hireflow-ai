// ============================================================
// JOBPILOT AI - SESSION GUARD (Tab-Switch Persistence Fix)
//
// Root cause this fixes: several pages treat ANY failed API
// response (a transient network hiccup, a stale request that
// resolves late after a background tab's timers were throttled,
// or a genuine 401) identically to a real, permanent logout —
// immediately overwriting the visible page with an error state
// or redirecting to login. The clearest concrete case: company-
// applicants.js's silent background poll (LIVE_STATE_POLL_INTERVAL_MS)
// wiped out the entire applicants table with "Not authenticated.
// Please log in again." on a single failed poll tick, even
// though the session was still perfectly valid a moment later.
//
// This module is the ONE centralized place that decides whether
// a 401 is genuine. It never touches assessment integrity
// (assessment-integrity.js) — it only ever reads auth state via
// the existing GET /api/auth/me endpoint and optionally redirects
// to the correct login page. It is safe to load on every
// authenticated page, including assessment pages, because it
// takes no action at all unless the session is confirmed dead.
//
// Usage from any page controller:
//
//   if (!response.ok) {
//     if (response.status === 401) {
//       const genuinelyLoggedOut = await HireFlowSessionGuard.isAuthFailureGenuine();
//       if (genuinelyLoggedOut) {
//         HireFlowSessionGuard.redirectToLogin();
//         return; // page is navigating away, stop here
//       }
//       // transient - fall through and treat like any other
//       // recoverable error (retry, soft banner, silent no-op)
//     }
//     ...
//   }
// ============================================================

(function () {

    if (window.HireFlowSessionGuard) {
        // Already installed by an earlier <script> on this page
        // (defensive against accidental double-include) - never
        // install a second, competing copy.
        return;
    }

    const AUTH_API_BASE_URL = "http://" + (window.location.hostname === "localhost" ? "localhost" : "127.0.0.1") + ":8000";

    // De-duplicates concurrent verification calls so multiple
    // simultaneous 401s (e.g. several requests failing at once
    // after a tab wakes up) trigger exactly ONE /api/auth/me
    // call, never a burst of them.
    let verifyInFlight = null;

    // Once true, redirectToLogin() becomes a no-op - prevents a
    // redirect loop or multiple competing redirects firing from
    // different callers in the same page.
    let redirecting = false;

    /* ==========================================================
       WHICH IDENTITY THIS PAGE ACTUALLY NEEDS

       Determined from THIS PAGE'S OWN FILENAME, never from
       ambient localStorage. Every company page is named
       company-*.html and every admin page admin-*.html across
       this whole project (see the <script src="js/session-guard.
       js"> include list) - that naming is a hard, load-bearing
       convention here, not a guess.

       Root-caused bug this replaces: localStorage is shared by
       EVERY tab on this origin, and jobpilot_company/jobpilot_
       admin are DELIBERATELY never cleared on a candidate login
       (a candidate tab and an HR tab are meant to both stay
       signed in at once - see the module header). So a candidate
       page used to ask "does a jobpilot_company key exist
       ANYWHERE in this browser's storage for this origin" and
       treat that as "this page must be a company page" - true the
       very first time you ever open a candidate page after having
       EVER logged into a company account on this origin, in ANY
       tab, even one closed days ago. That silently sent a
       perfectly valid, still-logged-in candidate to company-
       login.html on the next transient 401/tab-switch/back-
       forward-cache restore. The filename is unambiguous and
       already known the instant this script runs - no guessing
       needed.
       ========================================================== */

    function expectedIdentityKind() {

        const filename = (window.location.pathname.split("/").pop() || "").toLowerCase();

        if (filename.startsWith("admin-")) {
            return "admin";
        }

        if (filename.startsWith("company-")) {
            return "company";
        }

        return "user";
    }

    function loginPageForThisContext() {

        switch (expectedIdentityKind()) {

            case "admin":
                return "admin-login.html";

            case "company":
                return "company-login.html";

            default:
                return "index.html";
        }
    }

    /* ==========================================================
       VERIFY SESSION ALIVE

       The single source of truth for "is THIS page's own session
       still valid right now".

       IMPORTANT: GET /api/auth/me always resolves the candidate/
       admin identity (jobpilot_session) FIRST, and only falls
       back to the company identity (jobpilot_company_session) if
       no user session exists. That means on a browser that holds
       BOTH a valid candidate session and a valid company session
       at once (exactly the "different roles in different tabs"
       scenario this fix must support - see Step 9), calling
       /api/auth/me from a company page would always report the
       CANDIDATE identity and never prove anything about the
       company session at all - a dead company session sitting
       next to a healthy candidate one would look "alive" by
       mistake, and (worse) a perfectly healthy company session
       could be wrongly judged dead the moment its subject_type
       didn't match.

       So: for a company page, verification goes straight to a
       real, already-existing, lightweight COMPANY-only endpoint
       (get_current_company()-gated, same as every other company
       route) instead - its 200/401 answer is authoritative for
       the company cookie alone, with no ambiguity from whatever
       else this browser is signed into. Candidate and admin pages
       keep using /api/auth/me, which is already unambiguous for
       them (a browser can only ever hold one jobpilot_session at
       a time, so there is no second identity to confuse it with).

       Returns:
         true  - this page's session is valid (or the check
                 itself could not be completed, e.g. offline/
                 network error - a connectivity blip must never
                 be punished as a logout)
         false - the server has genuinely confirmed no valid
                 session of the kind this page needs exists
       ========================================================== */

    function companyIdFromStorage() {

        try {

            const stored = localStorage.getItem("jobpilot_company");

            if (!stored) {
                return null;
            }

            const parsed = JSON.parse(stored);

            return parsed && parsed.id ? parsed.id : null;

        } catch (error) {
            return null;
        }
    }

    function verifySessionAlive() {

        if (verifyInFlight) {
            return verifyInFlight;
        }

        const expected = expectedIdentityKind();

        let checkUrl = `${AUTH_API_BASE_URL}/api/auth/me`;
        let requireCompanyBody = false;

        if (expected === "company") {

            const companyId = companyIdFromStorage();

            if (companyId) {

                checkUrl =
                    `${AUTH_API_BASE_URL}/api/company-applications/` +
                    `${companyId}/filters/jobs`;

            } else {

                requireCompanyBody = true;
            }
        }

        verifyInFlight = fetch(
            checkUrl,
            {
                method: "GET",
                credentials: "include",
                cache: "no-store",
            }
        )
            .then(function (response) {

                if (expected !== "company" || requireCompanyBody) {

                    if (!response.ok) {
                        return false;
                    }

                    return response.json()
                        .then(function (body) {

                            if (requireCompanyBody) {
                                return body.subject_type === "company";
                            }

                            if (expected === "admin") {
                                return (
                                    body.subject_type === "user" &&
                                    body.role === "admin"
                                );
                            }

                            return body.subject_type === "user";
                        })
                        .catch(function () {
                            // Malformed/unreadable body - unknown,
                            // not disproven. Don't punish for it.
                            return true;
                        });
                }

                // Company path via the company-scoped endpoint:
                // its own status code is the whole answer.
                return response.ok;
            })
            .catch(function () {
                // Network/offline/aborted - unknown, not
                // disproven. Treat as still-alive so a temporary
                // connectivity problem never logs the user out.
                return true;
            })
            .finally(function () {
                verifyInFlight = null;
            });

        return verifyInFlight;
    }

    /* ==========================================================
       IS THIS 401 GENUINE

       response is optional - when omitted (e.g. a caller that
       only knows "I got a 401" without the Response object) this
       still works correctly.
       ========================================================== */

    async function isAuthFailureGenuine(response) {

        if (response && response.status !== 401) {
            return false;
        }

        const alive = await verifySessionAlive();

        return !alive;
    }

    /* ==========================================================
       REDIRECT TO LOGIN (guarded, fires at most once per page)
       ========================================================== */

    function redirectToLogin() {

        if (redirecting) {
            return;
        }

        redirecting = true;

        window.location.href = loginPageForThisContext();
    }

    /* ==========================================================
       ONE-CALL HELPER FOR THE COMMON CASE

       Pass the failed Response (or nothing) in; this both
       decides genuineness AND performs the guarded redirect if
       genuine. Returns true if the caller should stop (the page
       is navigating away), false if the caller should treat the
       failure as transient/recoverable and carry on.
       ========================================================== */

    async function handlePossibleAuthFailure(response) {

        const genuine = await isAuthFailureGenuine(response);

        if (genuine) {

            redirectToLogin();

            return true;
        }

        return false;
    }

    /* ==========================================================
       TAB VISIBILITY RECOVERY

       When the tab becomes visible again, silently re-verify the
       session so a GENUINE expiry (the user was logged out from
       another tab, an admin revoked the account, the absolute
       24-hour lifetime actually elapsed) is caught and handled
       through the exact same guarded, single-shot redirect —
       instead of waiting for the next incidental API call to
       stumble into it and potentially render a raw error state
       first.

       Deliberately does nothing else: no reload, no re-fetch of
       page data, no state reset. Each page's own controller
       remains responsible for refreshing its own live data (most
       pages already do this via their own polling where it
       matters, e.g. company-applicants.js) - this handler only
       ever protects the session check itself.

       This runs on EVERY page that loads this script, including
       assessment pages - it never interacts with
       assessment-integrity.js's own visibilitychange listener
       (a completely independent listener on the same event) and
       never suppresses or replaces proctoring behavior.
       ========================================================== */

    document.addEventListener("visibilitychange", function () {

        if (document.visibilityState !== "visible") {
            return;
        }

        verifySessionAlive().then(function (alive) {

            if (!alive) {
                redirectToLogin();
            }
        });
    });

    /* ==========================================================
       BACK/FORWARD CACHE RESTORE

       event.persisted === true means the browser restored this
       page from bfcache (a back/forward navigation) rather than
       re-running all scripts from scratch. The page's in-memory
       state is already intact - only the session needs a quiet
       re-check, never a reload.
       ========================================================== */

    window.addEventListener("pageshow", function (event) {

        if (!event.persisted) {
            return;
        }

        verifySessionAlive().then(function (alive) {

            if (!alive) {
                redirectToLogin();
            }
        });
    });

    window.HireFlowSessionGuard = {
        verifySessionAlive: verifySessionAlive,
        isAuthFailureGenuine: isAuthFailureGenuine,
        handlePossibleAuthFailure: handlePossibleAuthFailure,
        redirectToLogin: redirectToLogin,
    };

})();
