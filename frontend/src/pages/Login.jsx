import { useState } from "react";
import { loginUser } from "../services/api";

function Login({ onLogin, onForgot, onRegister }) {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();

    setError("");
    setLoading(true);

    try {
      const data = await loginUser(
        identifier,
        password
      );

      onLogin(data.user);

    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-background">
        <div className="auth-card">

          <div className="brand">
            <div className="brand-icon">🚀</div>
            <h1>HireFlow AI</h1>
            <p>Find the right job with AI</p>
          </div>

          <div className="auth-header">
            <h2>Welcome Back</h2>
            <p>Login to continue your job search</p>
          </div>

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit}>

            <label>
              Username, Email or Mobile
            </label>

            <input
              type="text"
              placeholder="Enter username, email or mobile"
              value={identifier}
              onChange={(e) =>
                setIdentifier(e.target.value)
              }
              required
            />

            <label>
              Password
            </label>

            <input
              type="password"
              placeholder="Enter your password"
              value={password}
              onChange={(e) =>
                setPassword(e.target.value)
              }
              required
            />

            <button
              type="submit"
              disabled={loading}
              className="primary-button"
            >
              {loading ? "Logging in..." : "Login"}
            </button>

          </form>

          <button
            className="link-button"
            onClick={onForgot}
          >
            Forgot Password?
          </button>

          <div className="divider">
            <span>OR</span>
          </div>

          <p className="register-text">
            Don't have an account?
          </p>

          <button
            className="secondary-button"
            onClick={onRegister}
          >
            Create Account
          </button>

        </div>
      </div>
    </div>
  );
}

export default Login;