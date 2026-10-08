import { useState } from "react";
import Login from "./pages/Login";
import Register from "./pages/Register";

function App() {
  const [page, setPage] = useState("login");
  const [user, setUser] = useState(null);

  function handleLogin(userData) {
    setUser(userData);
  }

  function handleRegistered() {
    setPage("login");
  }

  if (user) {
    return (
      <div style={{ padding: "40px" }}>
        <h1>Welcome, {user.full_name}!</h1>
        <p>You are successfully logged in.</p>
      </div>
    );
  }

  if (page === "register") {
    return (
      <Register
        onRegistered={handleRegistered}
        onLogin={() => setPage("login")}
      />
    );
  }

  return (
    <Login
      onLogin={handleLogin}
      onForgot={() => alert("Forgot password coming next")}
      onRegister={() => setPage("register")}
    />
  );
}

export default App;