const API_BASE_URL = "http://127.0.0.1:8000";


// ==========================================
// REGISTER
// ==========================================

export async function registerUser(userData) {
  const response = await fetch(
    `${API_BASE_URL}/api/users/register`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(userData),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Registration failed"
    );
  }

  return data;
}


// ==========================================
// LOGIN
// ==========================================

export async function loginUser(
  identifier,
  password
) {
  const response = await fetch(
    `${API_BASE_URL}/api/users/login`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        identifier: identifier,
        password: password,
      }),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Login failed"
    );
  }

  return data;
}


// ==========================================
// SEARCH JOBS
// ==========================================

export async function searchJobs(
  keyword = "Python Developer",
  location = "Chennai"
) {
  const params = new URLSearchParams({
    keyword: keyword,
    location: location,
    max_age_days: "30",
  });

  const response = await fetch(
    `${API_BASE_URL}/jobs/search?${params.toString()}`
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Job search failed"
    );
  }

  return data;
}


// ==========================================
// FORGOT PASSWORD
// ==========================================

export async function forgotPassword(
  identifier
) {
  const response = await fetch(
    `${API_BASE_URL}/api/password/forgot`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        identifier: identifier,
      }),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Could not send OTP"
    );
  }

  return data;
}


// ==========================================
// VERIFY OTP
// ==========================================

export async function verifyOTP(
  identifier,
  otp
) {
  const response = await fetch(
    `${API_BASE_URL}/api/password/verify-otp`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        identifier: identifier,
        otp: otp,
      }),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Invalid OTP"
    );
  }

  return data;
}


// ==========================================
// RESET PASSWORD
// ==========================================

export async function resetPassword(
  identifier,
  otp,
  newPassword,
  confirmPassword
) {
  const response = await fetch(
    `${API_BASE_URL}/api/password/reset`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        identifier: identifier,
        otp: otp,
        new_password: newPassword,
        confirm_password: confirmPassword,
      }),
    }
  );

  const data = await response.json();

  if (!response.ok) {
    throw new Error(
      data.detail || "Password reset failed"
    );
  }

  return data;
}