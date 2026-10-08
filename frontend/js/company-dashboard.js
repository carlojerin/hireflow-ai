document.addEventListener("DOMContentLoaded", () => {

    const API_BASE =
        "http://127.0.0.1:8000";

    const STORAGE_KEY =
        "jobpilot_company";

    let company = null;


    /* =========================================
       SESSION
    ========================================= */

    function getStoredCompany() {

        try {

            const stored =
                localStorage.getItem(
                    STORAGE_KEY
                );

            if (!stored) {
                return null;
            }

            return JSON.parse(
                stored
            );

        } catch (error) {

            console.error(
                "Unable to read company session:",
                error
            );

            return null;
        }
    }


    function saveStoredCompany(
        companyData
    ) {

        localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify(
                companyData
            )
        );
    }


    /* =========================================
       HELPERS
    ========================================= */

    function setText(
        element,
        value
    ) {

        if (!element) {
            return;
        }

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {

            element.textContent =
                "—";

            return;
        }

        element.textContent =
            value;
    }


    function formatStatus(
        value
    ) {

        if (!value) {
            return "—";
        }

        return value
            .replace(/_/g, " ")
            .replace(
                /\b\w/g,
                letter =>
                    letter.toUpperCase()
            );
    }


    function showComingSoon(
        message
    ) {

        alert(message);
    }


    /* =========================================
       ACCESS CHECK
    ========================================= */

    function checkCompanyAccess() {

        company =
            getStoredCompany();

        if (!company) {

            window.location.href =
                "company-login.html";

            return false;
        }


        if (
            company.role !==
            "company"
        ) {

            localStorage.removeItem(
                STORAGE_KEY
            );

            window.location.href =
                "company-login.html";

            return false;
        }


        if (
            company.verification_status !==
                "verified" ||
            !company.account_active ||
            !company.admin_verified
        ) {

            alert(
                "Your company account is not verified or active."
            );

            localStorage.removeItem(
                STORAGE_KEY
            );

            window.location.href =
                "company-login.html";

            return false;
        }


        return true;
    }


    /* =========================================
       RENDER COMPANY
    ========================================= */

    function renderCompany(
        data
    ) {

        if (!data) {
            return;
        }

        company =
            data;


        /* -----------------------------------------
           NAVBAR
        ----------------------------------------- */

        setText(
            document.getElementById(
                "navbarCompanyName"
            ),
            company.company_name ||
            "Company"
        );


        /* -----------------------------------------
           WELCOME
        ----------------------------------------- */

        setText(
            document.getElementById(
                "welcomeCompanyName"
            ),
            company.company_name ||
            "Welcome"
        );


        setText(
            document.getElementById(
                "welcomeLegalName"
            ),
            company.legal_name ||
            "Company"
        );


        /* -----------------------------------------
           AVATAR
        ----------------------------------------- */

        const avatar =
            document.getElementById(
                "companyAvatar"
            );


        if (avatar) {

            const firstLetter =
                (
                    company.company_name ||
                    "C"
                )
                    .trim()
                    .charAt(0)
                    .toUpperCase();


            if (
                company.company_logo
            ) {

                avatar.innerHTML =
                    "";


                const image =
                    document.createElement(
                        "img"
                    );


                image.src =
                    company.company_logo.startsWith(
                        "http"
                    )
                        ? company.company_logo
                        : `${API_BASE}${company.company_logo}`;


                image.alt =
                    `${
                        company.company_name ||
                        "Company"
                    } logo`;


                image.style.width =
                    "100%";

                image.style.height =
                    "100%";

                image.style.objectFit =
                    "cover";

                image.style.borderRadius =
                    "14px";


                avatar.appendChild(
                    image
                );

            } else {

                avatar.innerHTML =
                    "";

                avatar.textContent =
                    firstLetter;
            }
        }


        /* -----------------------------------------
           COMPANY INFORMATION
        ----------------------------------------- */

        setText(
            document.getElementById(
                "infoCompanyName"
            ),
            company.company_name
        );


        setText(
            document.getElementById(
                "infoLegalName"
            ),
            company.legal_name
        );


        setText(
            document.getElementById(
                "infoIndustry"
            ),
            company.industry
        );


        setText(
            document.getElementById(
                "infoLocation"
            ),
            company.location
        );


        setText(
            document.getElementById(
                "infoWebsite"
            ),
            company.website
        );


        setText(
            document.getElementById(
                "infoCompanySize"
            ),
            company.company_size
        );


        /* -----------------------------------------
           VERIFICATION
        ----------------------------------------- */

        setText(
            document.getElementById(
                "verificationStatus"
            ),
            formatStatus(
                company.verification_status
            )
        );


        setText(
            document.getElementById(
                "emailVerified"
            ),
            company.email_verified
                ? "Yes"
                : "No"
        );


        setText(
            document.getElementById(
                "mobileVerified"
            ),
            company.mobile_verified
                ? "Yes"
                : "No"
        );
    }


    /* =========================================
       LOAD LATEST COMPANY DATA
    ========================================= */

    async function loadCompany() {

        if (
            !company ||
            !company.id
        ) {
            return;
        }


        try {

            const response =
                await fetch(
                    `${API_BASE}/api/companies/${company.id}`,
                    {
                        method: "GET",
                        credentials: "include",
                        cache: "no-store"
                    }
                );


            let data;

            try {

                data =
                    await response.json();

            } catch {

                throw new Error(
                    "Invalid response from company API."
                );
            }


            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to load company information."
                );
            }


            if (
                data &&
                data.company
            ) {

                company =
                    data.company;


                saveStoredCompany(
                    company
                );


                renderCompany(
                    company
                );
            }


        } catch (error) {

            console.error(
                "COMPANY LOAD ERROR:",
                error
            );


            renderCompany(
                company
            );
        }
    }


    /* =========================================
       LOAD COMPANY DASHBOARD COUNTS
    ========================================= */

    async function loadDashboardCounts() {

        if (
            !company ||
            !company.id
        ) {
            return;
        }

        /* =========================================
           Real funnel numbers, from the SAME source
           company-candidate-tracking.html already uses
           (one row per invited candidate, with a computed
           level_category: invited/scheduled/not_started/
           in_progress/success/danger/expired). This invite-
           only product has no separate "application" pool,
           no real shortlist action distinct from inviting,
           and no candidate-view tracking at all -- the old
           cards counted relics of a job-board design this
           product no longer is. These four cards answer what
           HR actually asks: how many invited, how many yet to
           start, how many mid-assessment, how many finished.
        ========================================= */

        let candidates = [];

        try {

            const response = await fetch(
                `${API_BASE}/api/company-applications/${company.id}/candidate-tracking`,
                { method: "GET", credentials: "include", cache: "no-store" }
            );

            const data = await response.json().catch(() => ({}));

            if (response.ok && Array.isArray(data.candidates)) {
                candidates = data.candidates;
            }

        } catch (error) {
            console.error("Unable to load candidate tracking counts:", error);
        }

        const counts = { invited: 0, scheduled: 0, in_progress: 0, not_started: 0, success: 0, danger: 0, expired: 0 };

        candidates.forEach((c) => {
            if (counts[c.level_category] !== undefined) counts[c.level_category] += 1;
        });

        setText(document.getElementById("candidatesInvited"), candidates.length);
        setText(document.getElementById("notStartedCount"), counts.invited + counts.scheduled + counts.not_started + counts.expired);
        setText(document.getElementById("inProgressCount"), counts.in_progress);
        setText(document.getElementById("completedCount"), counts.success + counts.danger);
    }


    /* =========================================
       LOGOUT
    ========================================= */

    function logout() {

        const confirmed =
            window.confirm(
                "Are you sure you want to logout?"
            );


        if (!confirmed) {
            return;
        }


        // Best-effort: revoke the server-side session too.
        // Never blocks the local logout below.
        fetch(
            `${API_BASE}/api/auth/logout`,
            {
                method: "POST",
                credentials: "include",
            }
        ).catch(function (error) {

            console.error(
                "Server-side logout failed (non-blocking):",
                error
            );

        });


        localStorage.removeItem(
            STORAGE_KEY
        );


        window.location.href =
            "company-login.html";
    }


    /* =========================================
       NAVIGATION
    ========================================= */

    function setupNavigation() {

        const profileLink =
            document.getElementById(
                "profileLink"
            );


        const applicantsLink =
            document.getElementById(
                "applicantsLink"
            );


        /* -----------------------------------------
           HR PROFILE
        ----------------------------------------- */

        if (profileLink) {

            profileLink.addEventListener(
                "click",
                event => {

                    event.preventDefault();


                    window.location.href =
                        "company-profile.html";
                }
            );
        }


        /* -----------------------------------------
           APPLICANTS
        ----------------------------------------- */

        if (applicantsLink) {

            applicantsLink.addEventListener(
                "click",
                event => {

                    event.preventDefault();


                    window.location.href =
                        "company-candidate-tracking.html";
                }
            );
        }

        // Settings/Invite-a-Candidate are plain <a href> links now --
        // no click-handler override needed, they navigate normally.
    }


    /* =========================================
       QUICK ACTIONS
    ========================================= */

    function setupQuickActions() {

        const viewApplicantsAction =
            document.getElementById(
                "viewApplicantsAction"
            );


        /* -----------------------------------------
           VIEW APPLICANTS
        ----------------------------------------- */

        if (viewApplicantsAction) {

            viewApplicantsAction.addEventListener(
                "click",
                event => {

                    event.preventDefault();


                    window.location.href =
                        "company-candidate-tracking.html";
                }
            );
        }

        // "Invite a Candidate" is a plain <a href> link now -- no
        // click-handler override needed, it navigates normally.
    }


    /* =========================================
       EVENT LISTENERS
    ========================================= */

    const logoutButton =
        document.getElementById(
            "logoutButton"
        );


    if (logoutButton) {

        logoutButton.addEventListener(
            "click",
            event => {

                event.preventDefault();

                logout();
            }
        );
    }


    /* =========================================
       INITIALIZE
    ========================================= */

    if (
        !checkCompanyAccess()
    ) {
        return;
    }


    renderCompany(
        company
    );


    setupNavigation();


    setupQuickActions();


    /*
        Load latest company information
        and dashboard statistics.
    */

    loadCompany();


    loadDashboardCounts();

});