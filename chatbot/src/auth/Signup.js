import React, { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { authSignup, setToken } from "./authClient";
import TopHeader from "../components/TopHeader";
import Footer from "../components/Footer";
import BackArrow from "../components/BackArrow";
import { useAppDialog } from "../components/AppDialog";


const INDUSTRIES = [
  "Technology",
  "Telecommunications",
  "Finance",
  "Healthcare",
  "Education",
  "Retail",
  "Manufacturing",
  "Other",
];

export default function Signup() {
  const navigate = useNavigate();
  const location = useLocation();
  const { showAlert } = useAppDialog();

  const prefillEmail = useMemo(() => {
    return (location.state && location.state.email) || "";
  }, [location.state]);

  const [companyName, setCompanyName] = useState("");
  const [adminFullName, setAdminFullName] = useState("");
  const [adminEmail, setAdminEmail] = useState(prefillEmail);
  const [website, setWebsite] = useState("");
  const [industry, setIndustry] = useState("");

  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const result = await authSignup({
        company_name: companyName,
        admin_full_name: adminFullName,
        admin_email: adminEmail,
        website: website || null,
        industry: industry || null,
      });

      if (result.ok && result.data?.token) {
        setToken(result.data.token);
        navigate("/dashboard");
        return;
      }

      await showAlert(result.data?.message || "Sign-up failed. Please try again.", {
        title: "Sign-up failed",
        variant: "danger",
      });
    } catch (err) {
      console.error("Signup error:", err);
      await showAlert("Sign-up failed. Please try again.", {
        title: "Sign-up failed",
        variant: "danger",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <TopHeader leftTo="/PreLogin" rightSrc="/SLTMobitel_Logo.svg.png" rightAlt="SLTMobitel Logo" />

      <div className="login-container split-login-container">
        <BackArrow
          onClick={() => navigate("/PreLogin")}
          title="Back"
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
          <h2 className="features-side-title">Launch Your Workspace</h2>
          <p className="features-side-subtitle">
            SLT Digital Lab Multi-Tenant AI Platform
          </p>
          <div className="features-list">
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Isolated Tenant Spaces</strong>
                <p>Register your company and isolate all workflows, logs, and billing details safely.</p>
              </div>
            </div>
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Instant Agent Deployment</strong>
                <p>Deploy custom knowledge assistants to production in minutes.</p>
              </div>
            </div>
            <div className="feature-item">
              <span className="feature-icon"></span>
              <div>
                <strong>Secure Admin Controls</strong>
                <p>Complete control over users, payment gateways, and custom prompt roles.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right Side: Form */}
        <div className="login-form-side" style={{ padding: "40px" }}>
          <h2 className="login-title" style={{ marginBottom: "10px" }}>Register your company</h2>
          <p className="login-subtitle" style={{ fontSize: "13.5px", marginBottom: "20px" }}>
            Fill out the form below to create your company workspace
          </p>

          <form onSubmit={submit} style={{ width: "100%", maxWidth: "360px", margin: "0 auto" }}>
            <input
              type="text"
              placeholder="Company name *"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              required
              className="login-input"
            />

            <input
              type="text"
              placeholder="Admin full name *"
              value={adminFullName}
              onChange={(e) => setAdminFullName(e.target.value)}
              required
              className="login-input"
            />

            <input
              type="email"
              placeholder="Admin email *"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
              required
              className="login-input"
            />

            <input
              type="url"
              placeholder="Company website (optional)"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              className="login-input"
            />

            <select
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="login-input"
              style={{ height: 44, color: industry ? "#fff" : "#999" }}
            >
              <option value="">Industry (optional)</option>
              {INDUSTRIES.map((i) => (
                <option key={i} value={i} style={{ background: "#1c142c", color: "#fff" }}>
                  {i}
                </option>
              ))}
            </select>

            <button
              className="button google login-submit-btn"
              type="submit"
              disabled={loading}
              style={{ marginTop: "6px" }}
            >
              {loading ? "Creating workspace..." : "Create Workspace"}
            </button>

            <div style={{ marginTop: "14px", textAlign: "center", color: "#ccc", fontSize: "14px" }}>
              Already have a company?{" "}
              <span
                onClick={() => navigate("/login")}
                style={{ color: "#fff", textDecoration: "underline", cursor: "pointer", fontWeight: "600" }}
              >
                Sign in
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
}


