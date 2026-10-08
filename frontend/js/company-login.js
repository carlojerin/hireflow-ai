const API_BASE_URL = "http://127.0.0.1:8000";


/* ============================================================
   DOM ELEMENTS
============================================================ */

const companyEmailInput =
    document.getElementById("companyEmail");

const companyPasswordInput =
    document.getElementById("companyPassword");

const loginCompanyButton =
    document.getElementById("loginCompanyButton");

const messageBox =
    document.getElementById("messageBox");


/* ============================================================
   SHOW MESSAGE
============================================================ */

function showMessage(
    message,
    type = "danger"
) {

    if (!messageBox) {
        return;
    }


    messageBox.className =
        `alert alert-${type}`;

    messageBox.textContent =
        message;

    messageBox.classList.remove(
        "d-none"
    );
}


/* ============================================================
   HIDE MESSAGE
============================================================ */

function hideMessage() {

    if (!messageBox) {
        return;
    }


    messageBox.classList.add(
        "d-none"
    );
}


/* ============================================================
   SET LOGIN BUTTON LOADING
============================================================ */

function setLoginLoading(
    loading
) {

    if (!loginCompanyButton) {
        return;
    }


    if (loading) {

        loginCompanyButton.disabled =
            true;

        loginCompanyButton.innerHTML = `
            <span
                class="spinner-border spinner-border-sm me-2"
                role="status"
                aria-hidden="true"
            ></span>

            Logging in...
        `;

    } else {

        loginCompanyButton.disabled =
            false;

        loginCompanyButton.innerHTML = `
            <i class="bi bi-box-arrow-in-right me-2"></i>
            Login
        `;

    }
}


/* ============================================================
   LOGIN COMPANY
============================================================ */

async function loginCompany() {

    hideMessage();


    /* --------------------------------------------------------
       GET VALUES
    -------------------------------------------------------- */

    const email =
        companyEmailInput
            ? companyEmailInput.value.trim().toLowerCase()
            : "";

    const password =
        companyPasswordInput
            ? companyPasswordInput.value
            : "";


    /* --------------------------------------------------------
       VALIDATE EMAIL
    -------------------------------------------------------- */

    if (!email) {

        showMessage(
            "Please enter your email."
        );

        if (companyEmailInput) {
            companyEmailInput.focus();
        }

        return;
    }


    /* --------------------------------------------------------
       BASIC EMAIL VALIDATION
    -------------------------------------------------------- */

    if (
        !email.includes("@") ||
        !email.split("@")[1]?.includes(".")
    ) {

        showMessage(
            "Please enter a valid email address."
        );

        if (companyEmailInput) {
            companyEmailInput.focus();
        }

        return;
    }


    /* --------------------------------------------------------
       VALIDATE PASSWORD
    -------------------------------------------------------- */

    if (!password) {

        showMessage(
            "Please enter your password."
        );

        if (companyPasswordInput) {
            companyPasswordInput.focus();
        }

        return;
    }


    /* --------------------------------------------------------
       BUTTON LOADING
    -------------------------------------------------------- */

    setLoginLoading(true);


    try {

        /* ----------------------------------------------------
           API REQUEST
        ---------------------------------------------------- */

        const response =
            await fetch(
                `${API_BASE_URL}/api/companies/login`,
                {
                    method: "POST",

                    // Lets the browser store the
                    // HTTP-only session cookie the
                    // backend now sets on login.
                    credentials: "include",

                    headers: {
                        "Content-Type":
                            "application/json",

                        "Accept":
                            "application/json"
                    },

                    body: JSON.stringify({

                        identifier: email,

                        password: password

                    })
                }
            );


        /* ----------------------------------------------------
           READ RESPONSE
        ---------------------------------------------------- */

        let data = null;


        try {

            data =
                await response.json();

        } catch (jsonError) {

            data = null;

        }


        /* ----------------------------------------------------
           ERROR
        ---------------------------------------------------- */

        if (!response.ok) {

            if (data?.detail === "email_not_verified") {

                window.location.href =
                    "company-verify-email.html?email=" +
                    encodeURIComponent(email) +
                    "&resend=1";

                return;
            }

            const errorMessage =
                data?.detail ||
                "Unable to login. Please try again.";

            showMessage(
                errorMessage,
                response.status === 403
                    ? "warning"
                    : "danger"
            );

            return;
        }


        /* ----------------------------------------------------
           SUCCESS
        ---------------------------------------------------- */

        const company =
            data?.company;


        if (!company) {

            showMessage(
                "Login succeeded, but your account information was not returned.",
                "danger"
            );

            return;
        }


        /* ----------------------------------------------------
           SAVE COMPANY SESSION
        ---------------------------------------------------- */

        localStorage.setItem(
            "jobpilot_company",
            JSON.stringify(company)
        );


        /* ----------------------------------------------------
           SUCCESS MESSAGE
        ---------------------------------------------------- */

        showMessage(
            "Login successful. Redirecting...",
            "success"
        );


        /* ----------------------------------------------------
           REDIRECT
        ---------------------------------------------------- */

        setTimeout(
            function () {

                window.location.href =
                    "company-dashboard.html";

            },
            800
        );


    } catch (error) {

        console.error(
            "Company login error:",
            error
        );


        showMessage(
            "Unable to connect to the HireFlow server. Please make sure the backend is running.",
            "danger"
        );


    } finally {

        setLoginLoading(false);

    }
}


/* ============================================================
   LOGIN BUTTON
============================================================ */

if (loginCompanyButton) {

    loginCompanyButton.addEventListener(
        "click",
        function () {

            loginCompany();

        }
    );

}


/* ============================================================
   ENTER KEY SUPPORT
============================================================ */

function handleEnterKey(event) {

    if (
        event.key === "Enter"
    ) {

        event.preventDefault();

        loginCompany();

    }
}


if (companyEmailInput) {

    companyEmailInput.addEventListener(
        "keydown",
        handleEnterKey
    );

}


if (companyPasswordInput) {

    companyPasswordInput.addEventListener(
        "keydown",
        handleEnterKey
    );

}


/* ============================================================
   PAGE INITIALIZATION
============================================================ */

document.addEventListener(
    "DOMContentLoaded",
    function () {

        console.log(
            "Company login page initialized."
        );


        /* ----------------------------------------------------
           CLEAR OLD MESSAGE
        ---------------------------------------------------- */

        hideMessage();


        /* ----------------------------------------------------
           AUTO FOCUS
        ---------------------------------------------------- */

        if (companyEmailInput) {

            companyEmailInput.focus();

        }

    }
);