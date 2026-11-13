import React, { useEffect, useState } from "react";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { URL } from "../urls";
import {
    buildPackageFeatures,
    buildPackageLimitations,
    formatLimit,
    isUnlimited,
} from "../utils/subscriptionPackages";
import "./Pricing.css";

function LandingPagePricing() {
    const [plans, setPlans] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState("");

    useEffect(() => {
        const loadPlans = async () => {
            try {
                const response = await fetch(URL.SUBSCRIPTION_PACKAGES);
                if (!response.ok) throw new Error("Failed to load subscription packages");
                const data = await response.json();
                setPlans(Array.isArray(data.packages) ? data.packages : []);
            } catch (error) {
                console.error("Error loading pricing packages:", error);
                setLoadError("Unable to load pricing packages right now. Please try again later.");
            } finally {
                setLoading(false);
            }
        };

        loadPlans();
    }, []);

    const faqs = [
        {
            question: "What is included in each subscription package?",
            answer:
                "Starter is designed for a single agent with website knowledge only. Business adds documents, OneDrive, and Form Builder for growing teams. Enterprise unlocks unlimited agents, knowledge bases, websites, and Endpoint Tools for large organizations.",
        },
        {
            question: "How do monthly token limits work?",
            answer:
                "Each package includes a monthly token allowance shared across workspace conversations. Input and output tokens from model calls are counted and limits can be adjusted centrally by platform administrators.",
        },
        {
            question: "Can I upgrade my workspace later?",
            answer:
                "Yes. Sign in to your dashboard and choose a higher tier from the billing section. Upgrades are applied to your workspace after checkout.",
        },
        {
            question: "What is your refund policy?",
            answer:
                "If you cancel your subscription at any time, you will receive a refund of 40% of the amount you paid. Refunds will be processed to your original payment method.",
        },
        {
            question: "Is my corporate data secure?",
            answer:
                "Absolutely. Security is our top priority. We use secure tenant-isolation architecture so that your company's documents, conversations, and custom APIs are completely private and never shared, pooled, or used for training other public models.",
        },
    ];

    return (
        <div className="pricing-page">
            <Navbar />

            <div className="pricing-hero">
                <div className="pricing-eyebrow">
                    <span className="pricing-eyebrow-dot" />
                    Subscription Packages
                </div>
                <h1>
                    Choose Your <span>Workspace Plan</span>
                </h1>
                <p>
                    Compare Starter, Business, and Enterprise packages to find the right balance of agents,
                    knowledge sources, tools, and token capacity for your organization.
                </p>
            </div>

            <main className="pricing-main">
                {loading && (
                    <div className="pricing-loading">
                        <div className="loading-spinner" />
                        <p>Loading subscription packages...</p>
                    </div>
                )}

                {!loading && loadError && (
                    <div className="pricing-error">{loadError}</div>
                )}

                {!loading && !loadError && (
                    <div className="pricing-cards">
                        {plans.map((plan) => {
                            const features = buildPackageFeatures(plan);
                            const limitations = buildPackageLimitations(plan);
                            const priceLabel = Number(plan.price_monthly_usd || 0).toFixed(
                                Number(plan.price_monthly_usd || 0) % 1 === 0 ? 0 : 2
                            );

                            return (
                                <div
                                    key={plan.id}
                                    className={`pricing-card ${plan.popular ? "popular" : ""}`}
                                >
                                    {plan.popular && <div className="popular-badge">Most Popular</div>}

                                    <h3>{plan.name}</h3>
                                    <p className="pricing-card-desc">{plan.description}</p>

                                    <div className="pricing-card-price">
                                        ${priceLabel}
                                        <span>{plan.id === 'free' ? ` / ${plan.agent_expiry_days || 7} days` : "/mo"}</span>
                                    </div>

                                    <div className="pricing-card-limits">
                                        <span>{isUnlimited(plan.agent_limit) ? "Unlimited agents" : `${plan.agent_limit} agent${Number(plan.agent_limit) === 1 ? "" : "s"}`}</span>
                                        <span>{formatLimit(plan.monthly_tokens)} tokens</span>
                                    </div>

                                    <div className="features-list-title">Features Included:</div>
                                     <ul className="features-list">
                                         {features.map((feature, idx) => {
                                             const isSpecial = ["Web URL", "OneDrive", "SharePoint", "Form Builder", "APIs", "MCP"].some(term => feature.includes(term));
                                             return (
                                                 <li key={idx} className="feature-item">
                                                     {isSpecial ? (
                                                         <i className="fa-solid fa-plus feature-icon" style={{ color: '#b026ff', fontWeight: 'bold' }} />
                                                     ) : (
                                                         <i className="fa-solid fa-circle-check feature-icon" />
                                                     )}
                                                     <span>{feature}</span>
                                                 </li>
                                             );
                                         })}
                                         {limitations.map((limit, idx) => (
                                             <li key={`limit-${idx}`} className="feature-item limit">
                                                 <i className="fa-solid fa-xmark feature-icon limit-icon" style={{ color: 'rgba(239, 68, 68, 0.4)', textDecoration: 'none' }} />
                                                 <span>{limit}</span>
                                             </li>
                                         ))}
                                     </ul>
                                </div>
                            );
                        })}
                    </div>
                )}

                <div className="faq-section">
                    <h2 className="faq-title">Frequently Asked Questions</h2>
                    <div className="faq-grid">
                        {faqs.map((faq, i) => (
                            <div key={i} className="faq-item">
                                <h3 className="faq-question">{faq.question}</h3>
                                <p className="faq-answer">{faq.answer}</p>
                            </div>
                        ))}
                    </div>
                </div>
            </main>

            <Footer />
        </div>
    );
}

export default LandingPagePricing;
