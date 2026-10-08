const API_BASE_URL = "http://127.0.0.1:8000";

const params = new URLSearchParams(window.location.search);
const email = (params.get("email") || "").trim().toLowerCase();

const emailShown = document.getElementById("emailShown");
const codeInput = document.getElementById("codeInput");
const verifyButton = document.getElementById("verifyButton");
const resendButton = document.getElementById("resendButton");
const messageBox = document.getElementById("messageBox");

let resendTimer = null;


function showMessage(text, type) {
    messageBox.className = `alert alert-${type || "danger"}`;
    messageBox.textContent = text;
}


function startResendCountdown(seconds) {

    clearInterval(resendTimer);

    let left = seconds;
    resendButton.disabled = true;
    resendButton.textContent = `Resend code (${left}s)`;

    resendTimer = setInterval(function () {
        left -= 1;

        if (left <= 0) {
            clearInterval(resendTimer);
            resendButton.disabled = false;
            resendButton.textContent = "Resend code";
            return;
        }

        resendButton.textContent = `Resend code (${left}s)`;
    }, 1000);
}


async function post(path, body) {

    const response = await fetch(`${API_BASE_URL}/api/companies/${path}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify(body),
    });

    let data = null;
    try { data = await response.json(); } catch (error) { data = null; }

    return { ok: response.ok, status: response.status, data: data };
}


async function verify() {

    const code = codeInput.value.replace(/\D/g, "");

    if (!email) {
        showMessage("This page needs the email address you registered with. Go back to login and try again.");
        return;
    }

    if (code.length !== 6) {
        showMessage("Enter the 6-digit code from your email.");
        codeInput.focus();
        return;
    }

    verifyButton.disabled = true;

    try {

        const result = await post("verify-email", { email: email, code: code });

        if (!result.ok) {
            showMessage((result.data && result.data.detail) || "That code did not work. Please try again.");
            verifyButton.disabled = false;
            return;
        }

        showMessage("Email verified. Taking you to login...", "success");
        setTimeout(function () { window.location.href = "company-login.html"; }, 1200);

    } catch (error) {
        showMessage("Could not reach the HireFlow server. Make sure the backend is running.");
        verifyButton.disabled = false;
    }
}


async function resend() {

    if (!email) {
        showMessage("This page needs the email address you registered with. Go back to login and try again.");
        return;
    }

    resendButton.disabled = true;

    try {

        const result = await post("resend-verification", { email: email });
        showMessage((result.data && result.data.message) || "A new code is on its way.", "info");
        startResendCountdown((result.data && result.data.retry_after) || 60);

    } catch (error) {
        showMessage("Could not reach the HireFlow server. Make sure the backend is running.");
        resendButton.disabled = false;
    }
}


emailShown.textContent = email || "your email";
verifyButton.addEventListener("click", verify);
resendButton.addEventListener("click", resend);
codeInput.addEventListener("keydown", function (event) { if (event.key === "Enter") verify(); });
codeInput.addEventListener("input", function () { codeInput.value = codeInput.value.replace(/\D/g, "").slice(0, 6); });

// coming from a login attempt with an unverified account: a fresh code is sent straight away
if (params.get("resend") === "1" && email) {
    resend();
} else {
    startResendCountdown(60);
}

codeInput.focus();
