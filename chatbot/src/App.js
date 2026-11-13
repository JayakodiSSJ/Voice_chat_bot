import React, { useEffect, useState } from "react";
import { BrowserRouter as Router, Routes, Route, Navigate } from "react-router-dom";
// import PreLogin from "./components/PreLogin";
import Login from "./auth/Login";
import ResetPassword from "./auth/ResetPassword";
import Signup from "./auth/Signup";
import Dashboard from "./components/Dashboard";
import AdminDashboard from "./components/AdminDashboard";
import CustomAgentChat from './components/CustomAgentChat';
import VoiceAgentChat from './components/VoiceAgentChat';
import VoiceChatErrorBoundary from './components/VoiceChatErrorBoundary';
import Terms from './components/Terms';
import Privacy from "./components/Privacy";
import { fetchMe, getToken, onAuthChanged } from "./auth/authClient";
import RequireAuth from "./auth/RequireAuth";
import LandingPage from "./components/LandingPage";
import LandingPageCategories from "./components/LandingPageCategories";
import LandingPageAbout from "./components/LandingPageAbout";
import LandingPagePricing from "./components/LandingPagePricing.js";
import LandingPageContact from "./components/LandingPageContact";
import ProjectDemo from "./components/ProjectDemo";


const App = () => {
  const [user, setUser] = useState(null);
  const [company, setCompany] = useState(null);
  const [userRole, setUserRole] = useState(null);
  const [loading, setLoading] = useState(true);
  const [hasToken, setHasToken] = useState(!!getToken());
  const isSuperAdmin = user?.is_super_admin || false;

  useEffect(() => {
    const boot = async () => {
      try {
        const me = await fetchMe();
        if (me?.user) {
          setUser(me.user);
          setCompany(me.company || null);
          setUserRole(me.user.role || null);
        } else {
          setUser(null);
          setCompany(null);
          setUserRole(null);
        }
      } finally {
        setLoading(false);
      }
    };

    boot();

    const unsubscribe = onAuthChanged(() => {
      // Re-fetch auth context after login/logout without needing a hard refresh
      setHasToken(!!getToken());
      boot();
    });

    return () => unsubscribe();
  }, []);

  if (loading) {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        backgroundImage: 'url("./images.jpg")',
        backgroundSize: 'cover',
        backgroundRepeat: 'no-repeat',
        backgroundAttachment: 'fixed',
        color: 'white',
        fontSize: '18px'
      }}>
        Loading...
      </div>
    );
  }

  return (
    <Router>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        {/* PreLogin route hidden — preserved for future use */}
        {/* <Route path="/prelogin" element={<PreLogin />} /> */}

        {/* Login route */}
        <Route path="/login" element={<Login />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/signup" element={<Signup />} />

        {/* Static pages - accessible to everyone */}
        <Route path="/terms" element={<Terms />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/categories" element={<LandingPageCategories />} />
        <Route path="/about" element={<LandingPageAbout />} />
        <Route path="/pricing" element={<LandingPagePricing />} />
        <Route path="/contact" element={<LandingPageContact />} />
        <Route path="/demo" element={<ProjectDemo />} />

        {/* Protected routes - only for authenticated users */}
        {user || hasToken ? (
          <>
            <Route
              path="/dashboard"
              element={
                <RequireAuth>
                  {isSuperAdmin ? (
                    <Navigate to="/admin-dashboard" replace />
                  ) : (
                    <Dashboard userRole={userRole} user={user} company={company} />
                  )}
                </RequireAuth>
              }
            />

            {/* Admin Dashboard Route - Only accessible for admin users */}
            {userRole === "admin" && (
              <Route
                path="/admin-dashboard"
                element={
                  <RequireAuth>
                    <AdminDashboard />
                  </RequireAuth>
                }
              />
            )}

            {/* Agent Routes */}

            <Route path="/chat/:agentId" element={<RequireAuth><CustomAgentChat userRole={userRole} /></RequireAuth>} />
            <Route path="/voice-chat/:agentId" element={<RequireAuth><VoiceChatErrorBoundary><VoiceAgentChat userRole={userRole} /></VoiceChatErrorBoundary></RequireAuth>} />

            {/* Redirect unknown routes to dashboard for authenticated users */}
            <Route
              path="*"
              element={<Navigate to={isSuperAdmin ? "/admin-dashboard" : "/dashboard"} replace />}
            />
          </>
        ) : (
          <>
            <Route path="/dashboard" element={<Navigate to="/login" replace />} />
            <Route path="/admin-dashboard" element={<Navigate to="/login" replace />} />
            <Route path="/chat/:agentId" element={<Navigate to="/login" replace />} />
            <Route path="/voice-chat/:agentId" element={<Navigate to="/login" replace />} />

            {/* Redirect unknown routes to home for unauthenticated users */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        )}
      </Routes>
    </Router>
  );
};

export default App;
