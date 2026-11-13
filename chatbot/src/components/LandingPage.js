

import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import "./LandingPage.css";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { getToken } from "../auth/authClient";

// ---------- Hero Component ----------
const Hero = ({ onBrowseClick, onLearnMoreClick }) => (
  <section className="hero-section">
    <div className="hero-container">
      <div className="hero-content">

        {/* Badge */}
        <div className="hero-badge">
          <span className="hero-badge-dot" />
          The future of workspace automation
        </div>

        {/* Headline */}
        <h1 className="hero-title">
          Experience the Power of{" "}
          <br />
          <span className="hero-highlight">Aisle AI Agents</span>
        </h1>

        {/* Description */}
        <p className="hero-description">
          Empower Your Business with Custom AI Solutions.
          Tailor specialized agents to automate tasks, optimize workflows
          and solve complex industry challenges in one secure workspace.
        </p>

        {/* CTA Buttons */}
        <div className="hero-buttons">
          <button className="btn-primary" onClick={onBrowseClick}>
            Get Started <span style={{ fontSize: "1.1em" }}>→</span>
          </button>
          <button className="btn-secondary" onClick={onLearnMoreClick}>Learn More</button>
        </div>
      </div>
    </div>
  </section>
);

// ---------- Main Landing Page ----------
const LandingPageMain = () => {
  const navigate = useNavigate();

  // Lock scroll only while on landing page, restore when leaving
  useEffect(() => {
    document.body.classList.add("landing-no-scroll");
    return () => {
      document.body.classList.remove("landing-no-scroll");
    };
  }, []);

  const handleBrowseAgentsClick = () => {
    // If already signed in, skip login and go directly to dashboard
    if (getToken()) {
      navigate("/dashboard");
    } else {
      navigate("/login");
    }
  };

  const handleLearnMoreClick = () => {
    navigate("/demo");
  };

  return (
    <div className="landing-page">
      <Navbar />
      <Hero onBrowseClick={handleBrowseAgentsClick} onLearnMoreClick={handleLearnMoreClick} />
      <div className="landing-main" />
      <Footer />
    </div>
  );
};

export default LandingPageMain;