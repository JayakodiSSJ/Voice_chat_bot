import React from "react";
import Navbar from "./Navbar";
import Footer from "./Footer";
import "./AboutUs.css";

const AboutUs = () => {

  const values = [
    {
      icon: "💡",
      title: "Innovation First",
      description:
        "We push the boundaries of what's possible with AI, constantly exploring new frontiers and breakthrough technologies.",
    },
    {
      icon: "👥",
      title: "Workspace Isolation",
      description:
        "Our platform provides isolated, secure environments for each company to manage their proprietary knowledge and tools.",
    },
    {
      icon: "🛡️",
      title: "Trust & Security",
      description:
        "We maintain the highest standards of security and ethical AI practices to protect our users and their data.",
    },
    {
      icon: "❤️",
      title: "Business-Centric",
      description:
        "Every AI agent is designed to augment your specific business goals, ensuring technology serves your team's unique requirements.",
    },
  ];

  const stats = [

  ];

  return (
    <div className="about-page">
      <Navbar />

      {/* ── Hero ── */}
      <div className="about-hero">
        <div className="about-eyebrow">
          <span className="about-eyebrow-dot" />
          Who We Are
        </div>
        <h1>
          About <span>Aisle Agent</span>
        </h1>
        <p>
          We're empowering companies to build their own future with specialized AI.
          Our platform provides the tools and infrastructure to design, deploy, and
          manage custom AI assistants tailored to your unique business challenges.
        </p>
      </div>

      {/* ── Main content ── */}
      <main className="about-main">

        {/* Mission card */}
        <div className="about-card">
          <div className="about-card-heading">
            <span className="about-card-heading-bar" />
            {/* <span className="about-card-icon">🎯</span> */}
            <h2>Our Mission</h2>
          </div>
          <div className="about-mission-text">
            <p>
              To democratize artificial intelligence for enterprises by providing a
              powerful, self-service platform where any company can create and
              deploy highly specialized AI agents without complex development cycles.
            </p>
            <p>
              We believe every business deserves tailored intelligence. Our
              workspace-isolation approach ensures that your AI agents is built on
              your data, follows your rules, and helps your team achieve more.
            </p>
          </div>
        </div>

        {/* Stats panel */}
        <div className="about-stats-grid">
          {stats.map((stat, i) => (
            <div className="about-stat" key={i}>
              <div className="about-stat-icon">{stat.icon}</div>
              <span className="about-stat-number">{stat.number}</span>
              <span className="about-stat-label">{stat.label}</span>
            </div>
          ))}
        </div>

        {/* Values card */}
        <div className="about-card">
          <div className="about-card-heading">
            <span className="about-card-heading-bar" />
            {/* <span className="about-card-icon">✨</span> */}
            <h2>Our Values</h2>
          </div>
          <div className="about-values-grid">
            {values.map((value, i) => (
              <div className="about-value-item" key={i}>
                <div className="about-value-icon">{value.icon}</div>
                <h3 className="about-value-title">{value.title}</h3>
                <p className="about-value-desc">{value.description}</p>
              </div>
            ))}
          </div>
        </div>



      </main>

      <Footer />
    </div>
  );
};

export default AboutUs;