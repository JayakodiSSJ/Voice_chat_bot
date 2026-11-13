import React, { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { fetchMe } from "./authClient";

export default function RequireAuth({ children }) {
  const location = useLocation();
  const [checking, setChecking] = useState(true);
  const [ok, setOk] = useState(false);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const me = await fetchMe();
        if (!mounted) return;
        setOk(!!me?.user);
      } catch (error) {
        if (!mounted) return;
        console.error("Authentication check failed:", error);
        setOk(false);
      } finally {
        if (mounted) setChecking(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  if (checking) {
    return (
      <main
        role="status"
        aria-live="polite"
        style={{
          minHeight: "100vh",
          display: "grid",
          placeContent: "center",
          gap: 12,
          color: "#e8ecff",
          textAlign: "center",
          background: "rgba(6, 13, 40, 0.72)",
        }}
      >
        <span style={{ fontSize: 16, fontWeight: 600 }}>Checking your sign-in...</span>
        <span style={{ color: "rgba(227, 232, 255, 0.65)", fontSize: 13 }}>
          This should only take a moment.
        </span>
      </main>
    );
  }
  if (!ok) return <Navigate to="/login" replace state={{ from: location }} />;
  return children;
}




