import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { URL } from "../urls";
import Footer from "../components/Footer";

const ResetPassword = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = useMemo(() => (searchParams.get("token") || "").trim(), [searchParams]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordSet, setPasswordSet] = useState(false);

  useEffect(() => {
    const verifyToken = async () => {
      if (!token) {
        setError("Invalid reset link.");
        setLoading(false);
        return;
      }

      try {
        const res = await fetch(`${URL.AUTH_RESET_PASSWORD_VERIFY}?token=${encodeURIComponent(token)}`);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data?.message || "Invalid reset link.");
        }
        setEmail(data?.email || "");
      } catch (e) {
        setError(e.message || "Invalid reset link.");
      } finally {
        setLoading(false);
      }
    };

    verifyToken();
  }, [token]);

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch(URL.AUTH_RESET_PASSWORD, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.message || "Password reset failed.");
      }
      setMessage("Password set successfully. Redirecting to login...");
      setPasswordSet(true);
      setPassword("");
      setConfirmPassword("");
      setTimeout(() => navigate("/login", { replace: true }), 2000);
    } catch (e) {
      setError(e.message || "Password reset failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const goToLogin = () => {
    navigate("/login", { replace: true });
  };

  return (
    <>
      <div className="login-container">
        <h2 className="login-title">Set Password</h2>
        {loading && <p style={{ color: "#ccc" }}>Validating link...</p>}
        {!loading && error && <p style={{ color: "#ff8a8a" }}>{error}</p>}
        {!loading && message && <p style={{ color: "#9affb2" }}>{message}</p>}

        {!loading && !error && !passwordSet && (
          <form onSubmit={onSubmit} style={{ width: "100%", maxWidth: "340px", margin: "0 auto" }}>
            <p style={{ color: "#ccc", marginBottom: "12px", fontSize: "0.9rem" }}>
              Email: <strong>{email}</strong>
            </p>
            <input
              type="password"
              placeholder="New password (min. 8 characters)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={submitting}
              style={{
                width: "100%",
                padding: "12px 14px",
                borderRadius: "10px",
                border: "1px solid rgba(255,255,255,0.15)",
                background: "rgba(0,0,0,0.35)",
                color: "#fff",
                marginBottom: "12px",
                outline: "none",
                boxSizing: "border-box",
                opacity: submitting ? 0.6 : 1,
              }}
            />
            <input
              type="password"
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              disabled={submitting}
              style={{
                width: "100%",
                padding: "12px 14px",
                borderRadius: "10px",
                border: "1px solid rgba(255,255,255,0.15)",
                background: "rgba(0,0,0,0.35)",
                color: "#fff",
                marginBottom: "12px",
                outline: "none",
                boxSizing: "border-box",
                opacity: submitting ? 0.6 : 1,
              }}
            />
            <button
              className="button google"
              type="submit"
              disabled={submitting}
              style={{ width: "100%", margin: "0 auto", display: "block", opacity: submitting ? 0.7 : 1 }}
            >
              {submitting ? "Setting Password..." : "Set Password"}
            </button>
          </form>
        )}

        {!loading && !error && passwordSet && (
          <div style={{ width: "100%", maxWidth: "340px", margin: "0 auto", textAlign: "center" }}>
            <p style={{ color: "#9affb2", marginBottom: "20px", fontSize: "1rem" }}>
              Your password has been set successfully.
            </p>
            <p style={{ color: "#ccc", marginBottom: "20px" }}>
              You can now log in with your credentials.
            </p>
            <button
              onClick={goToLogin}
              className="button google"
              style={{ width: "100%" }}
            >
              Go to Login
            </button>
          </div>
        )}
      </div>
      <Footer />
    </>
  );
};

export default ResetPassword;
