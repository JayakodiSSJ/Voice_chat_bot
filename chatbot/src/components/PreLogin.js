import React from "react";
import { useNavigate } from "react-router-dom";
import "../css.css";
import Footer from './Footer';
import TopHeader from "./TopHeader";
import BackArrow from "./BackArrow";

import { fetchMe, getToken } from "../auth/authClient";

const PreLogin = () => {
  const navigate = useNavigate();

  const handleAuthenticatedAgents = async () => {
    if (getToken()) {
      const me = await fetchMe();
      const isSuperAdmin = me?.user?.is_super_admin || false;
      navigate(isSuperAdmin ? "/admin-dashboard" : "/dashboard");
    } else {
      navigate("/login");
    }
  };


  const handleBackToLanding = () => {
    navigate("/");
  };

  return (
    <>
      <style>{`
        .responsive-grid {
          display: flex;
          justify-content: center;
          gap: 40px;
          max-width: 400px;
          margin: 0 auto;
        }
        
        @media (max-width: 768px) {
          .responsive-grid {
            gap: 30px;
          }
        }
        
        @media (max-width: 480px) {
          .responsive-grid {
            gap: 20px;
          }
        }
      `}</style>

      <TopHeader leftTo="/PreLogin" />

      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        minHeight: '100vh',
        backgroundImage: 'url("./images.jpg")',
        backgroundSize: 'cover',
        backgroundRepeat: 'no-repeat',
        backgroundAttachment: 'fixed',
        color: 'white',
        fontFamily: '"Poppins", sans-serif',
        padding: '20px',
        paddingTop: '70px'
      }}>
        <div className="container" style={{ maxWidth: '1000px', width: '100%', position: 'relative' }}>
          <BackArrow
            onClick={handleBackToLanding}
            title="Back to landing page"
            style={{ position: "absolute", top: "30px", left: "30px", zIndex: 2 }}
          />

          <h1 style={{
            fontSize: '36px',
            marginBottom: '20px',
            fontWeight: '700',
            color: '#ffffff',
            letterSpacing: '2px',
            textAlign: 'center'
          }}>
            Build Your Own Aisle AI Agents
          </h1>

          <p style={{
            fontSize: '18px',
            color: '#ccc',
            marginBottom: '50px',
            lineHeight: '1.6',
            textAlign: 'center'
          }}>
            Sign in to manage your company workspace and create tailored AI assistants
          </p>

          <div className="responsive-grid">
            {/* Authenticated Agents Box */}
            <div
              onClick={handleAuthenticatedAgents}
              className="access-type-card authenticated-card"
              style={{
                background: 'rgba(255, 255, 255, 0.15)',
                borderRadius: '16px',
                padding: '40px 30px',
                cursor: 'pointer',
                transition: 'all 0.3s ease',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                textAlign: 'center',
                boxShadow: '0 8px 20px rgba(0, 0, 0, 0.3), 0 0 15px rgba(34, 139, 34, 0.6)',
                minHeight: '280px',
                backdropFilter: 'blur(10px)',
                border: '2px solid rgba(34, 139, 34, 0.3)'
              }}
            >
              <div style={{
                width: '80px',
                height: '80px',
                background: 'linear-gradient(135deg, #4CAF50, #45a049)',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                marginBottom: '25px',
                fontSize: '32px',
                boxShadow: '0 4px 15px rgba(0, 0, 0, 0.4)'
              }}>
                <img width="60" height="60" src="https://img.icons8.com/3d-fluency/94/lock-2.png" alt="lock-2" />
              </div>

              <h3 style={{
                fontSize: '24px',
                fontWeight: '600',
                color: '#fff',
                marginBottom: '15px',
                lineHeight: '1.3'
              }}>
                Aisle AI Workspace
              </h3>

              <p style={{
                fontSize: '16px',
                color: '#ccc',
                lineHeight: '1.5',
                marginBottom: '20px'
              }}>
                Access your secure environment to build, configure and deploy custom AI agents for your business needs.
              </p>

              <div style={{
                background: 'rgba(76, 175, 80, 0.2)',
                padding: '8px 16px',
                borderRadius: '20px',
                fontSize: '14px',
                color: '#4CAF50',
                fontWeight: '600'
              }}>
                Login Required
              </div>
            </div>

            {/* Non-Authenticated Agents Box */}
          </div>

          <div style={{
            marginTop: '40px',
            padding: '20px',
            background: 'rgba(255, 255, 255, 0.05)',
            borderRadius: '8px',
            textAlign: 'center'
          }}>
            <p style={{
              color: '#ccc',
              fontSize: '14px',
              margin: 0,
              lineHeight: '1.5'
            }}>
              Need help? Contact our Support Team for assistance with workspace access and agent creation.
            </p>
          </div>
        </div>
      </div>
      <Footer />
    </>
  );
};

export default PreLogin;
