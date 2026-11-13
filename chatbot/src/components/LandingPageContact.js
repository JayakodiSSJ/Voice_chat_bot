import React, { useState } from "react";
import Navbar from "./Navbar";
import Footer from "./Footer";
import { URL } from "../urls";
import "./Contact.css";

/* ---------------- Contact Page ---------------- */

const Contact = () => {
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    subject: "",
    message: "",
    category: "general",
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStatus, setSubmitStatus] = useState({ type: "", message: "" });

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSubmitting(true);
    setSubmitStatus({ type: "", message: "" });

    try {
      const response = await fetch(URL.CONTACT_SUBMIT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });

      const result = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(result.error || "Failed to submit your message");
      }

      const ackSent = result.acknowledgment_sent !== false;
      const ackError = result.acknowledgment_error;
      setSubmitStatus({
        type: "success",
        message: ackSent
          ? "Thank you. Your inquiry was submitted successfully."
          : `Thank you. Your inquiry was submitted successfully. We could not confirm acknowledgment email delivery right now, but our team has received your request.${ackError ? ` (${ackError})` : ""}`,
      });
      setFormData({ name: "", email: "", subject: "", message: "", category: "general" });
    } catch (error) {
      setSubmitStatus({
        type: "error",
        message: "✕ " + (error.message || "Something went wrong. Please try again."),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  return (
    <div className="contact-page">
      <Navbar />

      <main className="contact-main">
        <div className="contact-wrapper">

          {/* ── Left Panel ── */}
          <div className="contact-left">

            {/* Heading block */}
            <div className="contact-heading-block">
              <div className="contact-eyebrow">
                <span className="contact-eyebrow-dot" />
                We're here to help
              </div>
              <h1>
                Get in <span>Touch</span>
              </h1>
              <p>
                Have questions about building custom AI agents or need help with your company workspace?
                Our team is ready to assist you on your automation journey.
              </p>
            </div>

            {/* Info cards */}
            <div className="contact-info-cards">
              <div className="contact-info-card">
                <div className="contact-info-icon">📧</div>
                <div className="contact-info-text">
                  <strong>Email Us</strong>
                  <span>support@aisleagent.com</span>
                </div>
              </div>
              <div className="contact-info-card">
                <div className="contact-info-icon">⚡</div>
                <div className="contact-info-text">
                  <strong>Response Time</strong>
                  <span>Within 24 hours</span>
                </div>
              </div>
              <div className="contact-info-card">
                <div className="contact-info-icon">🌐</div>
                <div className="contact-info-text">
                  <strong>Support Hours</strong>
                  <span>Mon – Fri, 9 AM – 5 PM</span>
                </div>
              </div>
            </div>
          </div>

          {/* ── Right: Form Card ── */}
          <div className="contact-card">
            <h2 className="contact-card-title">Send Us a Message</h2>
            <p className="contact-card-subtitle">
              Fill in the details below and we'll get back to you shortly.
            </p>

            <form className="contact-form" onSubmit={handleSubmit}>

              {/* Status */}
              {submitStatus.message && (
                <div className={`contact-status ${submitStatus.type}`}>
                  {submitStatus.message}
                </div>
              )}

              {/* Name + Email */}
              <div className="contact-form-row">
                <div className="contact-field">
                  <label htmlFor="name">Full Name *</label>
                  <input
                    id="name"
                    type="text"
                    name="name"
                    required
                    placeholder="Jane Smith"
                    value={formData.name}
                    onChange={handleChange}
                  />
                </div>
                <div className="contact-field">
                  <label htmlFor="email">Email Address *</label>
                  <input
                    id="email"
                    type="email"
                    name="email"
                    required
                    placeholder="jane@example.com"
                    value={formData.email}
                    onChange={handleChange}
                  />
                </div>
              </div>

              {/* Category + Subject */}
              <div className="contact-form-row">
                <div className="contact-field">
                  <label htmlFor="category">Category</label>
                  <select
                    id="category"
                    name="category"
                    value={formData.category}
                    onChange={handleChange}
                  >
                    <option value="general">General Inquiry</option>
                    <option value="support">Technical Support</option>
                    <option value="sales">Sales Question</option>
                    <option value="partnership">Partnership</option>
                    <option value="feedback">Feedback</option>
                  </select>
                </div>
                <div className="contact-field">
                  <label htmlFor="subject">Subject *</label>
                  <input
                    id="subject"
                    type="text"
                    name="subject"
                    required
                    placeholder="How can we help?"
                    value={formData.subject}
                    onChange={handleChange}
                  />
                </div>
              </div>

              {/* Message */}
              <div className="contact-field">
                <label htmlFor="message">Message *</label>
                <textarea
                  id="message"
                  name="message"
                  required
                  rows={5}
                  placeholder="Tell us more about your question or issue..."
                  value={formData.message}
                  onChange={handleChange}
                />
              </div>

              {/* Submit */}
              <button
                type="submit"
                className="contact-submit-btn"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <>
                    <span style={{ opacity: 0.8 }}>Sending</span>
                    <span style={{ animation: "blink 1s infinite" }}>...</span>
                  </>
                ) : (
                  <>Send Message →</>
                )}
              </button>

            </form>
          </div>

        </div>
      </main>

      <Footer />
    </div>
  );
};

export default Contact;
