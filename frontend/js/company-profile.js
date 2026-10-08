document.addEventListener("DOMContentLoaded", () => {

    const API_BASE = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_company";


    /* =========================================
       ELEMENTS
    ========================================== */

    const navbarCompanyName =
        document.getElementById("navbarCompanyName");

    const companyAvatar =
        document.getElementById("companyAvatar");

    const headerCompanyName =
        document.getElementById("headerCompanyName");

    const headerHrName =
        document.getElementById("headerHrName");

    const hrNameInput =
        document.getElementById("hrName");

    const companyNameDisplay =
        document.getElementById("companyNameDisplay");

    const companyEmail =
        document.getElementById("companyEmail");

    const companyMobile =
        document.getElementById("companyMobile");

    const verificationStatus =
        document.getElementById("verificationStatus");

    const emailVerified =
        document.getElementById("emailVerified");

    const mobileVerified =
        document.getElementById("mobileVerified");

    const companyId =
        document.getElementById("companyId");

    const saveButton =
        document.getElementById("saveProfileButton");

    const logoutButton =
        document.getElementById("logoutButton");

    const messageBox =
        document.getElementById("messageBox");


    /* Logo */

    const logoPreview =
        document.getElementById("logoPreview");

    const logoFileInput =
        document.getElementById("logoFileInput");

    const uploadLogoButton =
        document.getElementById("uploadLogoButton");

    const removeLogoButton =
        document.getElementById("removeLogoButton");

    const logoFileName =
        document.getElementById("logoFileName");


    /* =========================================
       SETTINGS
    ========================================== */

    let company = null;

    const MAX_LOGO_SIZE =
        5 * 1024 * 1024;

    const ALLOWED_TYPES = [
        "image/jpeg",
        "image/png",
        "image/webp"
    ];


    /* =========================================
       SESSION
    ========================================== */

    function getStoredCompany() {

        try {

            const stored =
                localStorage.getItem(STORAGE_KEY);

            if (!stored) {
                return null;
            }

            return JSON.parse(stored);

        } catch (error) {

            console.error(
                "Unable to read company session:",
                error
            );

            return null;
        }
    }


    function saveStoredCompany(companyData) {

        localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify(companyData)
        );
    }


    /* =========================================
       UI HELPERS
    ========================================== */

    function setText(element, value) {

        if (!element) {
            return;
        }

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {

            element.textContent = "—";

            return;
        }

        element.textContent = value;
    }


    function setInputValue(element, value) {

        if (!element) {
            return;
        }

        element.value =
            value === null ||
            value === undefined
                ? ""
                : value;
    }


    function showMessage(
        message,
        type = "success"
    ) {

        if (!messageBox) {
            return;
        }

        messageBox.textContent =
            message;

        messageBox.className =
            `alert alert-${type}`;

        messageBox.style.display =
            "block";

        window.scrollTo({
            top: 0,
            behavior: "smooth"
        });

        setTimeout(() => {

            if (messageBox) {

                messageBox.style.display =
                    "none";
            }

        }, 5000);
    }


    function formatVerification(value) {

        return value
            ? "Verified"
            : "Not Verified";
    }


    function formatStatus(value) {

        if (!value) {
            return "—";
        }

        return value
            .replace(/_/g, " ")
            .replace(/\b\w/g, letter =>
                letter.toUpperCase()
            );
    }


    function getLogoUrl(logoPath) {

        if (!logoPath) {
            return null;
        }

        if (
            logoPath.startsWith("http://") ||
            logoPath.startsWith("https://")
        ) {

            return logoPath;
        }

        return `${API_BASE}${logoPath}`;
    }


    /* =========================================
       LOGO PREVIEW
    ========================================== */

    function renderLogoPreview(logoPath) {

        if (!logoPreview) {
            return;
        }

        const firstLetter =
            (
                company?.company_name ||
                "C"
            )
                .trim()
                .charAt(0)
                .toUpperCase();


        if (logoPath) {

            const image =
                document.createElement("img");

            image.src =
                getLogoUrl(logoPath);

            image.alt =
                `${company?.company_name || "Company"} logo`;

            image.onerror = () => {

                logoPreview.innerHTML =
                    "";

                logoPreview.textContent =
                    firstLetter;
            };

            logoPreview.innerHTML =
                "";

            logoPreview.appendChild(
                image
            );

        } else {

            logoPreview.innerHTML =
                "";

            logoPreview.textContent =
                firstLetter;
        }
    }


    function renderHeaderLogo(logoPath) {

        if (!companyAvatar) {
            return;
        }

        const firstLetter =
            (
                company?.company_name ||
                "C"
            )
                .trim()
                .charAt(0)
                .toUpperCase();


        if (logoPath) {

            const image =
                document.createElement("img");

            image.src =
                getLogoUrl(logoPath);

            image.alt =
                `${company?.company_name || "Company"} logo`;

            image.style.width =
                "100%";

            image.style.height =
                "100%";

            image.style.objectFit =
                "cover";

            image.onerror = () => {

                companyAvatar.innerHTML =
                    "";

                companyAvatar.textContent =
                    firstLetter;
            };

            companyAvatar.innerHTML =
                "";

            companyAvatar.appendChild(
                image
            );

        } else {

            companyAvatar.innerHTML =
                "";

            companyAvatar.textContent =
                firstLetter;
        }
    }


    function renderLogos(logoPath) {

        renderLogoPreview(logoPath);

        renderHeaderLogo(logoPath);

        if (removeLogoButton) {

            removeLogoButton.disabled =
                !logoPath;
        }
    }


    /* =========================================
       RENDER PROFILE
    ========================================== */

    function renderProfile(companyData) {

        if (!companyData) {
            return;
        }

        company =
            companyData;


        /* Navbar */

        setText(
            navbarCompanyName,
            company.company_name ||
            "Company"
        );


        /* Header */

        setText(
            headerCompanyName,
            company.company_name ||
            "Company"
        );

        setText(
            headerHrName,
            company.hr_name ||
            "HR"
        );


        /* Logo */

        renderLogos(
            company.company_logo
        );


        /* Form */

        setInputValue(
            hrNameInput,
            company.hr_name
        );

        setText(
            companyNameDisplay,
            company.company_name
        );


        /* Account */

        setText(
            companyEmail,
            company.email
        );

        setText(
            companyMobile,
            company.mobile
        );


        /* Verification */

        setText(
            verificationStatus,
            formatStatus(
                company.verification_status
            )
        );

        setText(
            emailVerified,
            formatVerification(
                company.email_verified
            )
        );

        setText(
            mobileVerified,
            formatVerification(
                company.mobile_verified
            )
        );

        setText(
            companyId,
            company.id
        );


        /* Logo status */

        if (logoFileName) {

            logoFileName.textContent =
                company.company_logo
                    ? "Current company logo is uploaded."
                    : "No logo uploaded.";
        }
    }


    /* =========================================
       ACCESS CHECK
    ========================================== */

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
            company.verification_status
                !== "verified" ||
            !company.account_active ||
            !company.admin_verified
        ) {

            showMessage(
                "Your company account is not verified or active.",
                "warning"
            );

            setTimeout(() => {

                window.location.href =
                    "company-login.html";

            }, 2000);

            return false;
        }


        return true;
    }


    /* =========================================
       LOAD COMPANY
    ========================================== */

    async function loadCompanyProfile() {

        if (!company || !company.id) {
            return;
        }


        try {

            const response =
                await fetch(
                    `${API_BASE}/api/companies/${company.id}`,
                    {
                        credentials: "include"
                    }
                );


            const data =
                await response.json();


            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to load company profile."
                );
            }


            if (data.company) {

                company =
                    data.company;

                saveStoredCompany(
                    company
                );

                renderProfile(
                    company
                );
            }


        } catch (error) {

            console.error(
                "Company profile load error:",
                error
            );

            renderProfile(
                company
            );
        }
    }


    /* =========================================
       SAVE PROFILE
    ========================================== */

    async function saveProfile() {

        if (!company || !company.id) {

            showMessage(
                "Company session not found. Please login again.",
                "danger"
            );

            return;
        }


        const hrName =
            hrNameInput?.value.trim() ||
            "";

        if (!hrName) {

            showMessage(
                "Your name is required.",
                "danger"
            );

            hrNameInput?.focus();

            return;
        }


        const profileData = {

            // Company name/legal name are read-only in this UI, so the
            // existing values are sent through unchanged -- the backend
            // endpoint still expects company_name on every save.
            company_name:
                company.company_name,

            legal_name:
                company.legal_name,

            hr_name:
                hrName,
        };


        const originalHTML =
            saveButton
                ? saveButton.innerHTML
                : "";


        try {

            if (saveButton) {

                saveButton.disabled =
                    true;

                saveButton.innerHTML = `
                    <span
                        class="spinner-border spinner-border-sm me-1"
                    ></span>
                    Saving...
                `;
            }


            const response =
                await fetch(
                    `${API_BASE}/api/companies/${company.id}/profile`,
                    {
                        method: "PUT",

                        credentials: "include",

                        headers: {
                            "Content-Type":
                                "application/json"
                        },

                        body:
                            JSON.stringify(
                                profileData
                            )
                    }
                );


            const data =
                await response.json();


            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to update company profile."
                );
            }


            if (data.company) {

                company =
                    data.company;

                saveStoredCompany(
                    company
                );

                renderProfile(
                    company
                );
            }


            showMessage(
                data.message ||
                "Company profile updated successfully.",
                "success"
            );


        } catch (error) {

            console.error(
                "Save profile error:",
                error
            );

            showMessage(
                error.message ||
                "Unable to update company profile.",
                "danger"
            );


        } finally {

            if (saveButton) {

                saveButton.disabled =
                    false;

                saveButton.innerHTML =
                    originalHTML ||
                    `
                    <i class="bi bi-check-lg me-1"></i>
                    Save Changes
                    `;
            }
        }
    }


    /* =========================================
       LOGO PICKER
    ========================================== */

    function openLogoPicker() {

        if (!logoFileInput) {
            return;
        }

        logoFileInput.click();
    }


    /* =========================================
       SELECT LOGO
    ========================================== */

    function handleLogoSelection() {

        if (!logoFileInput) {
            return;
        }


        const file =
            logoFileInput.files[0];


        if (!file) {
            return;
        }


        if (
            !ALLOWED_TYPES.includes(
                file.type
            )
        ) {

            showMessage(
                "Invalid logo format. Please choose JPG, PNG or WEBP.",
                "danger"
            );

            logoFileInput.value =
                "";

            return;
        }


        if (
            file.size >
            MAX_LOGO_SIZE
        ) {

            showMessage(
                "Logo size must be 5 MB or less.",
                "danger"
            );

            logoFileInput.value =
                "";

            return;
        }


        if (logoFileName) {

            logoFileName.textContent =
                `Selected: ${file.name}`;
        }


        const reader =
            new FileReader();


        reader.onload =
            event => {

                if (!logoPreview) {
                    return;
                }

                logoPreview.innerHTML =
                    "";

                const image =
                    document.createElement(
                        "img"
                    );

                image.src =
                    event.target.result;

                image.alt =
                    "Selected company logo";

                logoPreview.appendChild(
                    image
                );
            };


        reader.readAsDataURL(
            file
        );
    }


    /* =========================================
       UPLOAD LOGO
    ========================================== */

    async function uploadLogo() {

        if (!company || !company.id) {

            showMessage(
                "Company session not found. Please login again.",
                "danger"
            );

            return;
        }


        if (
            !logoFileInput ||
            !logoFileInput.files ||
            !logoFileInput.files[0]
        ) {

            openLogoPicker();

            return;
        }


        const file =
            logoFileInput.files[0];


        if (
            !ALLOWED_TYPES.includes(
                file.type
            )
        ) {

            showMessage(
                "Invalid logo format. Please choose JPG, PNG or WEBP.",
                "danger"
            );

            return;
        }


        if (
            file.size >
            MAX_LOGO_SIZE
        ) {

            showMessage(
                "Logo size must be 5 MB or less.",
                "danger"
            );

            return;
        }


        const formData =
            new FormData();


        formData.append(
            "file",
            file
        );


        const originalHTML =
            uploadLogoButton
                ? uploadLogoButton.innerHTML
                : "";


        try {

            if (uploadLogoButton) {

                uploadLogoButton.disabled =
                    true;

                uploadLogoButton.innerHTML = `
                    <span
                        class="spinner-border spinner-border-sm me-1"
                    ></span>
                    Uploading...
                `;
            }


            const response =
                await fetch(
                    `${API_BASE}/api/companies/${company.id}/logo`,
                    {
                        method: "POST",
                        credentials: "include",
                        body: formData
                    }
                );


            const data =
                await response.json();


            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to upload company logo."
                );
            }


            if (data.company) {

                company =
                    data.company;

                saveStoredCompany(
                    company
                );

                renderProfile(
                    company
                );
            }


            logoFileInput.value =
                "";


            showMessage(
                data.message ||
                "Company logo uploaded successfully.",
                "success"
            );


        } catch (error) {

            console.error(
                "Logo upload error:",
                error
            );

            showMessage(
                error.message ||
                "Unable to upload company logo.",
                "danger"
            );


        } finally {

            if (uploadLogoButton) {

                uploadLogoButton.disabled =
                    false;

                uploadLogoButton.innerHTML =
                    originalHTML ||
                    `
                    <i class="bi bi-upload me-1"></i>
                    Upload Logo
                    `;
            }
        }
    }


    /* =========================================
       REMOVE LOGO
    ========================================== */

    async function removeLogo() {

        if (!company || !company.id) {

            showMessage(
                "Company session not found. Please login again.",
                "danger"
            );

            return;
        }


        if (!company.company_logo) {

            showMessage(
                "There is no company logo to remove.",
                "info"
            );

            return;
        }


        const confirmed =
            window.confirm(
                "Are you sure you want to remove the company logo?"
            );


        if (!confirmed) {
            return;
        }


        const originalHTML =
            removeLogoButton
                ? removeLogoButton.innerHTML
                : "";


        try {

            if (removeLogoButton) {

                removeLogoButton.disabled =
                    true;

                removeLogoButton.innerHTML = `
                    <span
                        class="spinner-border spinner-border-sm me-1"
                    ></span>
                    Removing...
                `;
            }


            const response =
                await fetch(
                    `${API_BASE}/api/companies/${company.id}/logo`,
                    {
                        method: "DELETE",
                        credentials: "include"
                    }
                );


            const data =
                await response.json();


            if (!response.ok) {

                throw new Error(
                    data.detail ||
                    "Unable to remove company logo."
                );
            }


            if (data.company) {

                company =
                    data.company;

                saveStoredCompany(
                    company
                );

                renderProfile(
                    company
                );
            }


            if (logoFileInput) {

                logoFileInput.value =
                    "";
            }


            showMessage(
                data.message ||
                "Company logo removed successfully.",
                "success"
            );


        } catch (error) {

            console.error(
                "Logo remove error:",
                error
            );

            showMessage(
                error.message ||
                "Unable to remove company logo.",
                "danger"
            );


        } finally {

            if (removeLogoButton) {

                removeLogoButton.innerHTML =
                    originalHTML ||
                    `
                    <i class="bi bi-trash me-1"></i>
                    Remove Logo
                    `;

                removeLogoButton.disabled =
                    !company?.company_logo;
            }
        }
    }


    /* =========================================
       LOGOUT
    ========================================== */

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
       SIDEBAR NAVIGATION
    ========================================== */

    function setupNavigation() {

        // Applicants/Invite-a-Candidate/Settings are all plain <a href>
        // links now -- no click-handler override needed, they navigate
        // normally.
    }


    /* =========================================
       EVENT LISTENERS
    ========================================== */

    if (saveButton) {

        saveButton.addEventListener(
            "click",
            saveProfile
        );
    }


    if (uploadLogoButton) {

        uploadLogoButton.addEventListener(
            "click",
            uploadLogo
        );
    }


    if (logoFileInput) {

        logoFileInput.addEventListener(
            "change",
            handleLogoSelection
        );
    }


    if (removeLogoButton) {

        removeLogoButton.addEventListener(
            "click",
            removeLogo
        );
    }


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
    ========================================== */

    if (!checkCompanyAccess()) {
        return;
    }


    renderProfile(
        company
    );

    setupNavigation();

    loadCompanyProfile();

});