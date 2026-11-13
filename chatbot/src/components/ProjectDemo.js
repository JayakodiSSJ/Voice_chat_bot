import React from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "./Navbar";
import Footer from "./Footer";
import "./ProjectDemo.css";

const ProjectDemo = () => {
    const navigate = useNavigate();

    const capabilities = [
        {
            icon: "🏢",
            title: "Multi-Tenant Architecture",
            description: "Isolated workspace environments for each organization. Secure, scalable, and completely segregated data management."
        },
        {
            icon: "🤖",
            title: "Custom AI Agents",
            description: "Create specialized agents with unique personalities, system instructions, and specific cognitive capabilities."
        },
        {
            icon: "📚",
            title: "Knowledge Management",
            description: "Ingest PDFs and URLs to build a private knowledge base. Agents answer based on your proprietary data."
        },
        {
            icon: "🛠️",
            title: "Tool Integration",
            description: "Equip agents with real-world capabilities through custom API integrations and external tool connections."
        }
    ];

    const flowSteps = [
        {
            number: "01",
            title: "Organization Onboarding",
            description: "Tenants register their organization, creating a secure, isolated workspace instance with a dedicated dashboard."
        },
        {
            number: "02",
            title: "Knowledge Assembly",
            description: "Admins upload documents or sync external sources. The ingestion service processes and indexes data for RAG-based retrieval."
        },
        {
            number: "03",
            title: "Agent Orchestration",
            description: "Define agent behavior, select knowledge domains, and configure API tools to create a purpose-built AI colleague."
        },
        {
            number: "04",
            title: "Seamless Interaction",
            description: "Users interact with agents through a sleek chat interface, leveraging the combined power of LLMs and private knowledge."
        }
    ];

    return (
        <div className="project-demo">
            <Navbar />

            <main className="demo-content">
                {/* --- Hero --- */}
                <section className="demo-hero">
                    <div className="hero-badge">Aisle AI Project Showcase</div>
                    <h1>Architecting the Next Generation of AI Productivity</h1>
                    <p>
                        Aisle AI Agent Store is a comprehensive multi-tenant platform designed to
                        bridge the gap between advanced Large Language Models and enterprise-specific knowledge.
                    </p>

                </section>

                {/* --- Capabilities --- */}
                <section className="capabilities">
                    <div className="section-header">
                        <h2>Core Capabilities</h2>
                        <div className="line"></div>
                    </div>
                    <div className="capabilities-grid">
                        {capabilities.map((cap, i) => (
                            <div className={`capability-card ${cap.title === "Tool Integration" ? 'center-card' : ''}`} key={i}>
                                <div className="icon">{cap.icon}</div>
                                <h3>{cap.title}</h3>
                                <p>{cap.description}</p>
                            </div>
                        ))}
                    </div>
                </section>

                {/* --- User Flow --- */}
                <section className="user-flow-section">
                    <div className="section-header">
                        <h2>The User Flow</h2>
                        <div className="line"></div>
                    </div>
                    <div className="user-flow">
                        {flowSteps.map((step, i) => (
                            <div className="flow-step" key={i}>
                                <div className="step-number">{step.number}</div>
                                <div className="step-content">
                                    <h3>{step.title}</h3>
                                    <p>{step.description}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                {/* --- Get Started --- */}
                <section className="get-started-section">
                    <div className="section-header">
                        <h2>Ready to Get Started?</h2>
                        <div className="line"></div>
                        <p className="get-started-sub">
                            Already have an account? Sign in and get back to work.
                            New here? Create your free workspace in seconds.
                        </p>
                    </div>
                    <div className="get-started-buttons">
                        <button
                            id="demo-signin-btn"
                            className="gs-btn gs-btn-signin"
                            onClick={() => navigate("/login")}
                        >
                            <span className="gs-btn-icon"></span>
                            Sign In
                        </button>
                        <button
                            id="demo-signup-btn"
                            className="gs-btn gs-btn-signup"
                            onClick={() => navigate("/signup")}
                        >
                            <span className="gs-btn-icon"></span>
                            Sign Up — It's Free
                        </button>
                    </div>
                </section>
            </main>

            <Footer />
        </div>
    );
};

export default ProjectDemo;
