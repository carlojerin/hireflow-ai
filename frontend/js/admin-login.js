document.addEventListener("DOMContentLoaded", () => {

    const API_BASE_URL = "http://127.0.0.1:8000";

    const form = document.getElementById("adminLoginForm");
    const messageBox = document.getElementById("loginMessage");
    const loginButton = document.getElementById("loginButton");
    const passwordInput = document.getElementById("password");
    const togglePassword = document.getElementById("togglePassword");

    togglePassword.addEventListener("click", () => {
        const showing = passwordInput.type === "text";
        passwordInput.type = showing ? "password" : "text";
        togglePassword.querySelector("i").className = showing ? "bi bi-eye" : "bi bi-eye-slash";
        togglePassword.title = showing ? "Show password" : "Hide password";
        togglePassword.setAttribute("aria-label", showing ? "Show password" : "Hide password");
    });

    function showError(text) {
        messageBox.innerHTML = `<div class="error-box">${text}</div>`;
    }

    form.addEventListener("submit", async (event) => {

        event.preventDefault();

        const identifier = document.getElementById("identifier").value.trim();
        const password = document.getElementById("password").value;

        if (!identifier || !password) {
            showError("Enter both your email/username and password.");
            return;
        }

        loginButton.disabled = true;
        loginButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Signing in…';

        try {

            const response = await fetch(`${API_BASE_URL}/api/users/login`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ identifier, password }),
            });

            const data = await response.json();

            if (!response.ok) {
                showError(data.detail || "Invalid email/username or password.");
                return;
            }

            if (!data.user || data.user.role !== "admin") {
                showError("This account does not have admin access.");
                return;
            }

            localStorage.setItem("jobpilot_admin", JSON.stringify(data.user));
            window.location.href = "admin-dashboard.html";

        } catch (error) {
            showError("A network error occurred. Please try again.");
        } finally {
            loginButton.disabled = false;
            loginButton.innerHTML = '<i class="bi bi-shield-lock me-1"></i> Sign in';
        }
    });

});
