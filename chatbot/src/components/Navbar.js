import React, { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import "./Navbar.css";

/* ---------------- Navbar Component ---------------- */
const Navbar = ({ user, onSignOut, currentPage }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  // Dynamically determine which tab is active based on the URL path
  const path = location.pathname;
  const activeTab = currentPage || (
    path === "/" ? "home" :
      path.startsWith("/terms") ? "terms" :
        path.startsWith("/privacy") ? "privacy" :
          path.startsWith("/about") ? "about" :
            path.startsWith("/pricing") ? "pricing" :
              path.startsWith("/contact") ? "contact" : "home"
  );

  return (
    <header className="navbar-header">
      <div className="navbar-container">
        {/* Left: Hamburger + Logo + Desktop Nav */}
        <div className="navbar-left">
          {/* 1. Moved the mobile menu button here */}
          <button
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            className="mobile-menu-button"
          >
            {isMobileMenuOpen ? "✖" : "☰"}
          </button>

          {/* 2. Logo is now second */}
          <button onClick={() => navigate("/")} className="logo-button">
            <img
              src="/aisleaiagents.png"
              alt="Aisle Agent Logo"
              className="logo-image"
            />
          </button>

          <nav className="nav-desktop">
            <div className="navbar-links">
              {/* (All your <a> tags) */}
              <a
                href="/"
                className={`nav-link ${activeTab === "home" ? "active" : ""}`}
              >
                Home
              </a>
              <a
                href="/terms"
                className={`nav-link ${activeTab === "terms" ? "active" : ""}`}
              >
                Terms & Conditions
              </a>
              <a
                href="/privacy"
                className={`nav-link ${activeTab === "privacy" ? "active" : ""}`}
              >
                Privacy
              </a>
              <a
                href="/about"
                className={`nav-link ${activeTab === "about" ? "active" : ""}`}
              >
                About
              </a>
              <a
                href="/pricing"
                className={`nav-link ${activeTab === "pricing" ? "active" : ""}`}
              >
                Pricing
              </a>
              <a
                href="/contact"
                className={`nav-link ${activeTab === "contact" ? "active" : ""}`}
              >
                Contact
              </a>
            </div>
          </nav>
        </div>

        {/* Right: Logo Only */}
        <div className="navbar-right">
          <img
            src="/SLTMobitel_Logo.svg.png"
            alt="SLTMobitel Logo"
            className="logo-image"
          />
        </div>
      </div>

      {/* Mobile Menu Dropdown */}
      {isMobileMenuOpen && (
        <div className="mobile-menu-dropdown">
          <nav className="nav-mobile">
            <div className="navbar-links-mobile">
              {/* (All your mobile <a> tags) */}
              <a
                href="/"
                className={`mobile-nav-link ${activeTab === "home" ? "active" : ""}`}
              >
                Home
              </a>
              <a
                href="/terms"
                className={`mobile-nav-link ${activeTab === "terms" ? "active" : ""}`}
              >
                Terms & Conditions
              </a>
              <a
                href="/privacy"
                className={`mobile-nav-link ${activeTab === "privacy" ? "active" : ""}`}
              >
                Privacy
              </a>
              <a
                href="/about"
                className={`mobile-nav-link ${activeTab === "about" ? "active" : ""}`}
              >
                About
              </a>
              <a
                href="/pricing"
                className={`mobile-nav-link ${activeTab === "pricing" ? "active" : ""}`}
              >
                Pricing
              </a>
              <a
                href="/contact"
                className={`mobile-nav-link ${activeTab === "contact" ? "active" : ""}`}
              >
                Contact
              </a>
            </div>
          </nav>
        </div>
      )}
    </header>
  );
};

export default Navbar;
