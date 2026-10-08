import { useState } from "react";
import { registerUser } from "../services/api";

function Register({ onRegistered, onLogin }) {

  const [form, setForm] = useState({
    full_name: "",
    username: "",
    email: "",
    mobile: "",
    password: "",
    confirm_password: "",
    location: "",
    education: "",
    graduation_year: "",
    experience_level: "Fresher",
    preferred_role: "",
    preferred_work_mode: "On-site"
  });

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  function handleChange(event) {
    const { name, value } = event.target;

    setForm({
      ...form,
      [name]: value
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setLoading(true);

    try {

      const data = await registerUser({
        ...form,
        graduation_year:
          Number(form.graduation_year)
      });

      onRegistered(data.user);

    } catch (err) {

      setError(err.message);

    } finally {

      setLoading(false);

    }
  }

  return (
    <div className="auth-page">

      <div className="register-card">

        <div className="brand">
          <div className="brand-icon">🚀</div>
          <h1>HireFlow AI</h1>
          <p>Create your career profile</p>
        </div>

        <div className="auth-header">
          <h2>Create Account</h2>
          <p>Tell us about yourself</p>
        </div>

        {error && (
          <div className="error-message">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit}>

          <div className="form-grid">

            <div>
              <label>Full Name</label>
              <input
                name="full_name"
                placeholder="Jerin R"
                value={form.full_name}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Username</label>
              <input
                name="username"
                placeholder="jerin_r"
                value={form.username}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Email</label>
              <input
                type="email"
                name="email"
                placeholder="you@example.com"
                value={form.email}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Mobile Number</label>
              <input
                name="mobile"
                placeholder="9876543210"
                value={form.mobile}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Password</label>
              <input
                type="password"
                name="password"
                placeholder="Minimum 8 characters"
                value={form.password}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Confirm Password</label>
              <input
                type="password"
                name="confirm_password"
                placeholder="Confirm password"
                value={form.confirm_password}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Location</label>
              <input
                name="location"
                placeholder="Chennai"
                value={form.location}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Education</label>
              <input
                name="education"
                placeholder="B.Sc Computer Science"
                value={form.education}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Graduation Year</label>
              <input
                type="number"
                name="graduation_year"
                placeholder="2026"
                value={form.graduation_year}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Experience Level</label>
              <select
                name="experience_level"
                value={form.experience_level}
                onChange={handleChange}
              >
                <option>Fresher</option>
                <option>0-1 Years</option>
                <option>1-3 Years</option>
                <option>3-5 Years</option>
                <option>5+ Years</option>
              </select>
            </div>

            <div>
              <label>Preferred Job Role</label>
              <input
                name="preferred_role"
                placeholder="Python Developer"
                value={form.preferred_role}
                onChange={handleChange}
                required
              />
            </div>

            <div>
              <label>Preferred Work Mode</label>
              <select
                name="preferred_work_mode"
                value={form.preferred_work_mode}
                onChange={handleChange}
              >
                <option>On-site</option>
                <option>Hybrid</option>
                <option>Remote</option>
              </select>
            </div>

          </div>

          <button
            type="submit"
            disabled={loading}
            className="primary-button"
          >
            {loading
              ? "Creating Account..."
              : "Create Account"}
          </button>

        </form>

        <p className="login-text">
          Already have an account?
        </p>

        <button
          className="secondary-button"
          onClick={onLogin}
        >
          Login
        </button>

      </div>

    </div>
  );
}

export default Register;