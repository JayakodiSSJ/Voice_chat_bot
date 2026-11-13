import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FaEye, FaEyeSlash } from 'react-icons/fa';
import { useMsal } from "@azure/msal-react";
import { authLogin, authAzureLogin, fetchMe, setToken } from "./authClient";
import TopHeader from "../components/TopHeader";
import BackArrow from "../components/BackArrow";
import Footer from "../components/Footer";
import { useAppDialog } from "../components/AppDialog";

const Login = () => {
  const navigate = useNavigate();
  const { instance } = useMsal();
  const { showAlert } = useAppDialog();
  const [loading, setLoading] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const togglePasswordVisibility = () => {
    setShowPassword(!showPassword);
  };

  const handleBackToLanding = () => {
    navigate("/");
  };

  const handleMicrosoftLogin = async () => {
    setLoading(true);

    try {
      // Force account selection so it doesn't auto-pick the wrong Microsoft account
      // Request only basic user profile and email scopes for login
      const loginResponse = await instance.loginPopup({
        scopes: ["User.Read", "openid", "profile", "email"],
        prompt: "select_account"
      });

      const userEmail = (loginResponse.account?.username || "").trim().toLowerCase();

      // Basic domain check before sending to backend to give early feedback
      if (!userEmail.endsWith("@intranet.slt.com.lk") && !userEmail.endsWith("@slt.com.lk")) {
        setLoading(false);
        await showAlert("Azure AD login is restricted to Super Admins only.", {
          title: "Access denied",
          variant: "danger",
        });
        return;
      }

      // Extract the access token directly from the popup response
      const accessToken = loginResponse.accessToken;

      const result = await authAzureLogin(accessToken);

      if (result.ok && result.data?.token) {
        setToken(result.data.token);
        if (loginResponse.account) {
          instance.setActiveAccount(loginResponse.account);
        }
        const me = await fetchMe();
        const isSuperAdmin = me?.user?.is_super_admin || false;
        setLoading(false);
        navigate(isSuperAdmin ? "/admin-dashboard" : "/dashboard", { replace: true });
        return;
      }

      await showAlert(result.data?.message || "Azure AD login failed. Please try again.", {
        title: "Login failed",
        variant: "danger",
      });
    } catch (error) {
      console.error("Microsoft Login Error:", error);
      if (!error.message?.includes("user_cancelled")) {
        await showAlert(error.message || "Failed to log in with Microsoft. Please try again.", {
          title: "Login failed",
          variant: "danger",
        });
      }
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      const result = await authLogin({ email, password });

      if (result.ok && result.data?.token) {
        setToken(result.data.token);
        const me = await fetchMe();
        const isSuperAdmin = me?.user?.is_super_admin || false;
        setLoading(false);
        navigate(isSuperAdmin ? "/admin-dashboard" : "/dashboard", { replace: true });
        return;
      }

      if (result.status === 404 && result.data?.error === "company_not_found") {
        setLoading(false);
        navigate("/signup", { state: { email } });
        return;

      }

      const errorMessage =
        result.status === 401
          ? "Invalid email or password."
          : result.status >= 500
            ? "The login service is temporarily unavailable. Please try again later."
            : result.data?.message || "Unable to sign in. Please check your details and try again.";
      await showAlert(errorMessage, {
        title: "Login failed",
        variant: "danger",
      });
    } catch (error) {
      console.error("Login error:", error);
      const errorMessage =
        error.name === "AbortError"
          ? "The login request timed out. Please try again."
          : error instanceof TypeError
            ? "Could not connect to the login service. Check your network or backend connection and try again."
            : "An unexpected error occurred while signing in. Please try again.";
      await showAlert(errorMessage, {
        title: "Login failed",
        variant: "danger",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <TopHeader leftTo="/" rightSrc="/SLTMobitel_Logo.svg.png" rightAlt="SLTMobitel Logo" />

      <div className="login-container split-login-container">
        <BackArrow
          onClick={handleBackToLanding}
          title="Back to home"
          style={{ position: "absolute", top: "16px", left: "16px", zIndex: 2 }}
        />

        {/* Left Side: Brand features & mascot */}
        <div className="login-features-side">
          <div className="login-glow-orb" />
          <img
            src="/3d-chatbot-mascot.jpg"
            alt="AI assistant mascot"
            className="login-mascot"
          />
          <h2 className="features-side-title">Empower Your Workspace</h2>
          <p className="features-side-subtitle">
            SLT Digital Lab Multi-Tenant AI Platform
          </p>
          <div className="features-list">
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Custom AI Agents</strong>
                <p>Build and customize agents with specific roles and personality prompts.</p>
              </div>
            </div>
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Knowledge Base Sync</strong>
                <p>Sync website URLs, OneDrive files, or SharePoint documents directly.</p>
              </div>
            </div>
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Advanced Integrations</strong>
                <p>Configure custom APIs, endpoint actions, and interactive forms.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right Side: Form */}
        <div className="login-form-side">
          <h2 className="login-title">Welcome to Agent Store</h2>
          <p className="login-subtitle">
            Sign in with your company email to access your workspace
          </p>

          <form onSubmit={handleLogin} style={{ width: '100%', maxWidth: '340px', margin: '0 auto' }}>
            <input
              type="email"
              placeholder="Work email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="login-input"
            />
            <div style={{ position: 'relative', marginBottom: '14px', width: '100%' }}>
              <input
                type={showPassword ? "text" : "password"}
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className="login-input"
                style={{ paddingRight: '40px' }}
              />
              <button
                type="button"
                onClick={togglePasswordVisibility}
                style={{
                  position: 'absolute',
                  right: '10px',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'none',
                  border: 'none',
                  color: 'rgba(255,255,255,0.7)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  padding: 0
                }}
              >
                {showPassword ? <FaEyeSlash size={20} color="#ccc" /> : <FaEye size={20} color="#ccc" />}
              </button>
            </div>

            <button
              className="button google login-submit-btn"
              type="submit"
              disabled={loading}
            >
              {loading ? 'Signing in...' : 'Sign In'}
            </button>

            <div className="login-divider">
              <span>OR</span>
            </div>

            <button
              type="button"
              className="microsoft-login-btn"
              onClick={handleMicrosoftLogin}
              disabled={loading}
            >
              <svg className="ms-logo" viewBox="0 0 23 23" xmlns="http://www.w3.org/2000/svg">
                <path fill="#f35325" d="M0 0h11v11H0z" />
                <path fill="#81bc06" d="M12 0h11v11H12z" />
                <path fill="#05a6f0" d="M0 12h11v11H0z" />
                <path fill="#ffba08" d="M12 12h11v11H12z" />
              </svg>
              Super Admin Login
            </button>

            <div style={{ marginTop: '16px', textAlign: 'center', color: '#ccc', fontSize: '14px' }}>
              New company?{" "}
              <span
                onClick={() => navigate("/signup")}
                style={{ color: '#fff', textDecoration: 'underline', cursor: 'pointer', fontWeight: '600' }}
              >
                Register your company
              </span>
            </div>
          </form>
        </div>

      </div>
      <Footer />

      <style>
        {`
          .split-login-container {
            display: flex;
            flex-direction: row;
            width: 95%;
            max-width: 960px;
            min-height: 580px;
            padding: 0 !important;
            overflow: hidden;
            background: rgba(12, 8, 24, 0.85) !important;
            border: 1px solid rgba(146, 0, 220, 0.25) !important;
            box-shadow: 0 20px 50px rgba(0,0,0,0.6) !important;
          }

          .login-features-side {
            flex: 1.1;
            background: linear-gradient(135deg, #180d2b, #090615);
            border-right: 1px solid rgba(146, 0, 220, 0.15);
            padding: 40px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            position: relative;
            text-align: center;
          }

          .login-glow-orb {
            position: absolute;
            width: 200px;
            height: 200px;
            background: rgba(146, 0, 220, 0.25);
            border-radius: 50%;
            filter: blur(50px);
            z-index: 1;
          }

          .login-mascot {
            width: 90px;
            height: 90px;
            border-radius: 50%;
            object-fit: cover;
            border: 2px solid rgba(146, 0, 220, 0.4);
            box-shadow: 0 0 20px rgba(146, 0, 220, 0.5);
            z-index: 2;
            margin-bottom: 20px;
            animation: floatMascot 4s ease-in-out infinite;
          }

          @keyframes floatMascot {
            0% { transform: translateY(0px); }
            50% { transform: translateY(-8px); }
            100% { transform: translateY(0px); }
          }

          .features-side-title {
            color: #fff;
            font-size: 24px;
            font-weight: 700;
            margin: 0 0 4px;
            z-index: 2;
          }

          .features-side-subtitle {
            color: rgba(255, 255, 255, 0.5);
            font-size: 13px;
            margin: 0 0 30px;
            z-index: 2;
            text-transform: uppercase;
            letter-spacing: 1px;
          }

          .features-list {
            display: flex;
            flex-direction: column;
            gap: 16px;
            width: 100%;
            max-width: 380px;
            text-align: left;
            z-index: 2;
          }

          .feature-item {
            display: flex;
            align-items: flex-start;
            gap: 12px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255,255,255,0.05);
            padding: 12px;
            border-radius: 12px;
            transition: all 0.3s ease;
          }

          .feature-item:hover {
            background: rgba(146, 0, 220, 0.08);
            border-color: rgba(146, 0, 220, 0.3);
            transform: translateX(4px);
          }

          .feature-icon {
            font-size: 20px;
            margin-top: 2px;
          }

          .feature-item strong {
            color: #fff;
            font-size: 13.5px;
            display: block;
            margin-bottom: 2px;
          }

          .feature-item p {
            color: rgba(255,255,255,0.45);
            font-size: 11.5px;
            margin: 0;
            line-height: 1.4;
          }

          .login-form-side {
            flex: 0.9;
            padding: 50px 40px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            z-index: 2;
          }

          .login-subtitle {
            color: rgba(255,255,255,0.6);
            margin-bottom: 25px;
            font-size: 14.5px;
            line-height: 1.5;
            text-align: center;
          }

          .login-input {
            width: 100%;
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid rgba(146, 0, 220, 0.2);
            background: rgba(0,0,0,0.4);
            color: #fff;
            margin-bottom: 12px;
            outline: none;
            box-sizing: border-box;
            font-size: 14px;
            transition: all 0.3s ease;
          }

          .login-input:focus {
            border-color: #9200dc;
            box-shadow: 0 0 10px rgba(146, 0, 220, 0.2);
            background: rgba(0,0,0,0.55);
          }

          .login-submit-btn {
            width: 100%;
            padding: 12px;
            font-size: 15px;
            font-weight: 700;
            border-radius: 10px;
            background: #9200dc !important;
            border: none !important;
            color: #fff !important;
            cursor: pointer;
            transition: all 0.3s ease !important;
            margin-top: 10px;
          }

          .login-submit-btn:hover:not(:disabled) {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(146, 0, 220, 0.4) !important;
          }

          .login-divider {
            display: flex;
            align-items: center;
            text-align: center;
            margin: 18px 0;
            color: rgba(255, 255, 255, 0.4);
            font-size: 12px;
            font-weight: 600;
          }

          .login-divider::before,
          .login-divider::after {
            content: '';
            flex: 1;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
          }

          .login-divider:not(:empty)::before {
            margin-right: .5em;
          }

          .login-divider:not(:empty)::after {
            margin-left: .5em;
          }

          .microsoft-login-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            width: 100%;
            padding: 12px;
            font-size: 14px;
            font-weight: 600;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.08) !important;
            border: 1px solid rgba(255, 255, 255, 0.15) !important;
            color: #fff !important;
            cursor: pointer;
            transition: all 0.3s ease !important;
            margin-bottom: 10px;
          }

          .microsoft-login-btn:hover:not(:disabled) {
            background: rgba(255, 255, 255, 0.15) !important;
            border-color: rgba(255, 255, 255, 0.3) !important;
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(255, 255, 255, 0.05) !important;
          }

          .ms-logo {
            width: 18px;
            height: 18px;
            flex-shrink: 0;
          }

          @media (max-width: 768px) {
            .split-login-container {
              flex-direction: column;
              max-width: 500px;
            }
            .login-features-side {
              display: none;
            }
            .login-form-side {
              padding: 40px 24px;
            }
          }
        `}
      </style>
    </div>
  );
};

export default Login;
