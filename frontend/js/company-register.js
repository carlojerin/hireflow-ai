const API_BASE_URL = "http://127.0.0.1:8000";


/* ============================================================
   DOM ELEMENTS
============================================================ */

const companyNameInput =
    document.getElementById("companyName");

const hrNameInput =
    document.getElementById("hrName");

const companyEmailInput =
    document.getElementById("companyEmail");

const companyMobileInput =
    document.getElementById("companyMobile");

const companyPasswordInput =
    document.getElementById("companyPassword");

const confirmPasswordInput =
    document.getElementById("confirmPassword");

const registerCompanyButton =
    document.getElementById(
        "registerCompanyButton"
    );

const messageBox =
    document.getElementById(
        "messageBox"
    );


/* ============================================================
   SHOW / HIDE MESSAGE
============================================================ */

function showMessage(message, type = "danger") {

    if (!messageBox) return;

    messageBox.className = `alert alert-${type}`;
    messageBox.textContent = message;
    messageBox.classList.remove("d-none");
}

function hideMessage() {

    if (!messageBox) return;

    messageBox.classList.add("d-none");
}


/* ============================================================
   SET BUTTON LOADING
============================================================ */

function setRegisterLoading(loading) {

    if (!registerCompanyButton) return;

    if (loading) {

        registerCompanyButton.disabled = true;

        registerCompanyButton.innerHTML = `
            <span
                class="spinner-border spinner-border-sm me-2"
                role="status"
                aria-hidden="true"
            ></span>

            Creating Account...
        `;

    } else {

        registerCompanyButton.disabled = false;

        registerCompanyButton.innerHTML = `
            <i class="bi bi-building-add me-2"></i>

            Create Account
        `;
    }
}


/* ============================================================
   VALIDATE REGISTRATION
============================================================ */

function validateRegistration() {

    const companyName = companyNameInput ? companyNameInput.value.trim() : "";
    const hrName = hrNameInput ? hrNameInput.value.trim() : "";
    const email = companyEmailInput ? companyEmailInput.value.trim().toLowerCase() : "";
    const mobile = companyMobileInput ? companyMobileInput.value.trim() : "";
    const password = companyPasswordInput ? companyPasswordInput.value : "";
    const confirmPassword = confirmPasswordInput ? confirmPasswordInput.value : "";

    if (!companyName) {
        return "Company name is required.";
    }

    if (companyName.length < 2) {
        return "Company name must contain at least 2 characters.";
    }

    if (!hrName) {
        return "Your name is required.";
    }

    if (hrName.length < 2) {
        return "Your name must contain at least 2 characters.";
    }

    if (!email) {
        return "Your email is required.";
    }

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!emailPattern.test(email)) {
        return "Please enter a valid email address.";
    }

    if (!mobile) {
        return "Mobile number is required.";
    }

    const digitsOnly = mobile.replace(/\D/g, "");

    if (digitsOnly.length < 10 || digitsOnly.length > 20) {
        return "Please enter a valid mobile number.";
    }

    if (!password) {
        return "Password is required.";
    }

    if (password.length < 8) {
        return "Password must contain at least 8 characters.";
    }

    if (!confirmPassword) {
        return "Please confirm your password.";
    }

    if (password !== confirmPassword) {
        return "Passwords do not match.";
    }

    return null;
}


/* ============================================================
   REGISTER COMPANY
============================================================ */

async function registerCompany() {

    hideMessage();

    const validationError = validateRegistration();

    if (validationError) {
        showMessage(validationError, "warning");
        return;
    }

    const requestData = {
        company_name: companyNameInput.value.trim(),
        hr_name: hrNameInput.value.trim(),
        email: companyEmailInput.value.trim().toLowerCase(),
        mobile: companyMobileInput.value.trim(),
        password: companyPasswordInput.value,
        confirm_password: confirmPasswordInput.value,
    };

    setRegisterLoading(true);

    try {

        const response = await fetch(
            `${API_BASE_URL}/api/companies/register`,
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body: JSON.stringify(requestData),
            }
        );

        let data = null;

        try {
            data = await response.json();
        } catch (jsonError) {
            data = null;
        }

        if (!response.ok) {

            let errorMessage = "Unable to create your account. Please try again.";

            if (data && typeof data.detail === "string") {
                errorMessage = data.detail;
            } else if (data && Array.isArray(data.detail)) {
                errorMessage = data.detail
                    .map(errorItem => errorItem.msg || "Invalid input")
                    .join(" ");
            }

            showMessage(errorMessage, response.status === 400 ? "warning" : "danger");
            return;
        }

        const company = data?.company;

        if (!company) {
            showMessage(
                "Account created, but your account information was not returned.",
                "warning"
            );
            return;
        }

        showMessage("Account created successfully. Redirecting to login...", "success");

        const formControls = [
            companyNameInput,
            hrNameInput,
            companyEmailInput,
            companyMobileInput,
            companyPasswordInput,
            confirmPasswordInput,
        ];

        formControls.forEach(input => {
            if (input) input.disabled = true;
        });

        document.querySelectorAll(".password-toggle").forEach(button => {
            button.disabled = true;
        });

        if (registerCompanyButton) {

            registerCompanyButton.disabled = true;

            registerCompanyButton.innerHTML = `
                <i class="bi bi-check-circle me-2"></i>

                Account Created
            `;
        }

        setTimeout(() => {
            const email = encodeURIComponent((data && data.email) || (companyEmailInput ? companyEmailInput.value.trim().toLowerCase() : ""));
            window.location.href = "company-verify-email.html?email=" + email;
        }, 1200);

    } catch (error) {

        console.error("Company registration error:", error);

        showMessage(
            "Unable to connect to the HireFlow server. Please make sure the backend is running.",
            "danger"
        );

    } finally {

        if (registerCompanyButton && !registerCompanyButton.disabled) {
            setRegisterLoading(false);
        }
    }
}


/* ============================================================
   REGISTER BUTTON + ENTER KEY SUPPORT
============================================================ */

if (registerCompanyButton) {

    registerCompanyButton.addEventListener("click", event => {
        event.preventDefault();
        registerCompany();
    });
}

const registrationInputs = [
    companyNameInput,
    hrNameInput,
    companyEmailInput,
    companyMobileInput,
    companyPasswordInput,
    confirmPasswordInput,
];

registrationInputs.forEach(input => {

    if (!input) return;

    input.addEventListener("keydown", event => {

        if (event.key === "Enter") {
            event.preventDefault();
            registerCompany();
        }
    });
});


/* ============================================================
   PAGE INITIALIZATION
============================================================ */

document.addEventListener("DOMContentLoaded", () => {

    console.log("Company registration page initialized.");

    hideMessage();
});
