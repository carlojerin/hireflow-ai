document.addEventListener("DOMContentLoaded", () => {

    const API_BASE = "http://127.0.0.1:8000";
    const STORAGE_KEY = "jobpilot_company";

    const navbarCompanyName = document.getElementById("navbarCompanyName");
    const accountEmailInput = document.getElementById("accountEmail");
    const accountUsernameInput = document.getElementById("accountUsername");
    const saveAccountButton = document.getElementById("saveAccountButton");

    const currentPasswordInput = document.getElementById("currentPassword");
    const newPasswordInput = document.getElementById("newPassword");
    const confirmNewPasswordInput = document.getElementById("confirmNewPassword");
    const changePasswordButton = document.getElementById("changePasswordButton");

    const logoutButton = document.getElementById("logoutButton");
    const messageBox = document.getElementById("messageBox");

    let company = null;

    function getStoredCompany() {

        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            return stored ? JSON.parse(stored) : null;
        } catch (error) {
            console.error("Unable to read company session:", error);
            return null;
        }
    }

    function saveStoredCompany(companyData) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(companyData));
    }

    function showMessage(message, type = "success") {

        if (!messageBox) return;

        messageBox.textContent = message;
        messageBox.className = `alert alert-${type}`;
        messageBox.style.display = "block";

        window.scrollTo({ top: 0, behavior: "smooth" });

        setTimeout(() => {
            if (messageBox) messageBox.style.display = "none";
        }, 5000);
    }

    function checkCompanyAccess() {

        company = getStoredCompany();

        if (!company) {
            window.location.href = "company-login.html";
            return false;
        }

        if (company.role !== "company") {
            localStorage.removeItem(STORAGE_KEY);
            window.location.href = "company-login.html";
            return false;
        }

        return true;
    }

    function renderAccount() {

        if (!company) return;

        if (navbarCompanyName) {
            navbarCompanyName.textContent = company.company_name || "Company";
        }

        if (accountEmailInput) accountEmailInput.value = company.email || "";
        if (accountUsernameInput) accountUsernameInput.value = company.username || "";
    }

    async function saveAccount() {

        if (!company || !company.id) {
            showMessage("Company session not found. Please login again.", "danger");
            return;
        }

        const email = accountEmailInput?.value.trim().toLowerCase() || "";
        const username = accountUsernameInput?.value.trim() || "";

        if (!email) {
            showMessage("Email is required.", "danger");
            accountEmailInput?.focus();
            return;
        }

        if (!username) {
            showMessage("Username is required.", "danger");
            accountUsernameInput?.focus();
            return;
        }

        const originalHTML = saveAccountButton ? saveAccountButton.innerHTML : "";

        try {

            if (saveAccountButton) {
                saveAccountButton.disabled = true;
                saveAccountButton.innerHTML = `
                    <span class="spinner-border spinner-border-sm me-1"></span>
                    Saving...
                `;
            }

            const response = await fetch(
                `${API_BASE}/api/companies/${company.id}/account`,
                {
                    method: "PUT",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email, username }),
                }
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Unable to update account settings.");
            }

            if (data.company) {
                company = data.company;
                saveStoredCompany(company);
                renderAccount();
            }

            showMessage(data.message || "Account settings updated successfully.", "success");

        } catch (error) {

            console.error("Save account error:", error);
            showMessage(error.message || "Unable to update account settings.", "danger");

        } finally {

            if (saveAccountButton) {
                saveAccountButton.disabled = false;
                saveAccountButton.innerHTML = originalHTML || `
                    <i class="bi bi-check-lg me-1"></i>
                    Save Account
                `;
            }
        }
    }

    async function changePassword() {

        if (!company || !company.id) {
            showMessage("Company session not found. Please login again.", "danger");
            return;
        }

        const currentPassword = currentPasswordInput?.value || "";
        const newPassword = newPasswordInput?.value || "";
        const confirmNewPassword = confirmNewPasswordInput?.value || "";

        if (!currentPassword) {
            showMessage("Current password is required.", "danger");
            currentPasswordInput?.focus();
            return;
        }

        if (!newPassword || newPassword.length < 8) {
            showMessage("New password must be at least 8 characters.", "danger");
            newPasswordInput?.focus();
            return;
        }

        if (newPassword !== confirmNewPassword) {
            showMessage("New passwords do not match.", "danger");
            confirmNewPasswordInput?.focus();
            return;
        }

        const originalHTML = changePasswordButton ? changePasswordButton.innerHTML : "";

        try {

            if (changePasswordButton) {
                changePasswordButton.disabled = true;
                changePasswordButton.innerHTML = `
                    <span class="spinner-border spinner-border-sm me-1"></span>
                    Changing...
                `;
            }

            const response = await fetch(
                `${API_BASE}/api/companies/${company.id}/change-password`,
                {
                    method: "POST",
                    credentials: "include",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        current_password: currentPassword,
                        new_password: newPassword,
                    }),
                }
            );

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || "Unable to change password.");
            }

            if (currentPasswordInput) currentPasswordInput.value = "";
            if (newPasswordInput) newPasswordInput.value = "";
            if (confirmNewPasswordInput) confirmNewPasswordInput.value = "";

            showMessage(data.message || "Password changed successfully.", "success");

        } catch (error) {

            console.error("Change password error:", error);
            showMessage(error.message || "Unable to change password.", "danger");

        } finally {

            if (changePasswordButton) {
                changePasswordButton.disabled = false;
                changePasswordButton.innerHTML = originalHTML || `
                    <i class="bi bi-shield-lock me-1"></i>
                    Change Password
                `;
            }
        }
    }

    function logout() {

        const confirmed = window.confirm("Are you sure you want to logout?");
        if (!confirmed) return;

        fetch(`${API_BASE}/api/auth/logout`, {
            method: "POST",
            credentials: "include",
        }).catch(error => {
            console.error("Server-side logout failed (non-blocking):", error);
        });

        localStorage.removeItem(STORAGE_KEY);
        window.location.href = "company-login.html";
    }

    if (saveAccountButton) {
        saveAccountButton.addEventListener("click", saveAccount);
    }

    if (changePasswordButton) {
        changePasswordButton.addEventListener("click", changePassword);
    }

    if (logoutButton) {
        logoutButton.addEventListener("click", event => {
            event.preventDefault();
            logout();
        });
    }

    if (!checkCompanyAccess()) return;

    renderAccount();
});
