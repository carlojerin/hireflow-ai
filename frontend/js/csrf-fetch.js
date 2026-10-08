// ==================================================
// JOBPILOT AI - CSRF HEADER INJECTION (Task 3D-4)
//
// Centralized so no individual page/controller has to attach
// the CSRF header itself. Wraps the global fetch() once: for
// any state-changing request (POST/PUT/PATCH/DELETE), reads
// the non-HttpOnly "csrf_token" cookie the backend issues at
// login (and refreshes on every /api/auth/me call) and adds it
// as the X-CSRF-Token header, matching what
// backend/app/api/deps.py's require_csrf() checks against the
// same cookie server-side.
//
// Never touches the HttpOnly jobpilot_session cookie — that one
// is not, and must never be, readable from JavaScript.
//
// Load this before any page controller script that performs an
// authenticated mutation, so the very first fetch() call is
// already covered.
// ==================================================

(function () {

    const CSRF_COOKIE_NAME = "csrf_token";
    const CSRF_HEADER_NAME = "X-CSRF-Token";
    const STATE_CHANGING_METHODS = ["POST", "PUT", "PATCH", "DELETE"];

    function readCsrfCookie() {

        const prefix = CSRF_COOKIE_NAME + "=";

        const cookies = document.cookie.split(";");

        for (let i = 0; i < cookies.length; i++) {

            const cookie = cookies[i].trim();

            if (cookie.indexOf(prefix) === 0) {

                return decodeURIComponent(
                    cookie.substring(prefix.length)
                );
            }
        }

        return null;
    }

    const originalFetch = window.fetch.bind(window);

    window.fetch = function (input, init) {

        const options = init || {};

        const method = (options.method || "GET").toUpperCase();

        if (STATE_CHANGING_METHODS.indexOf(method) !== -1) {

            const csrfToken = readCsrfCookie();

            if (csrfToken) {

                const headers = new Headers(options.headers || {});

                headers.set(CSRF_HEADER_NAME, csrfToken);

                options.headers = headers;
            }
        }

        return originalFetch(input, options);
    };

})();
