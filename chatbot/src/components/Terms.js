
import React from 'react';
import '../components/Terms&Privacy.css';
import Footer from './Footer';
import Navbar from './Navbar';

function Term() {
  return (
    <div className="terms-page">
      <Navbar />

      {/* ── Hero Header ── */}
      <div className="terms-hero">
        <div className="terms-eyebrow">
          <span className="terms-eyebrow-dot" />
          Legal
        </div>
        <h1>Terms &amp; <span>Conditions</span></h1>
        <p className="terms-hero-meta">Last Updated: June 20, 2025</p>
      </div>

      {/* ── Two-column layout ── */}
      <div className="terms-main">

        {/* Sticky sidebar */}
        <aside className="terms-sidebar">
          <p className="terms-sidebar-title">On this page</p>
          <nav>
            <a href="#definitions">Definitions</a>
            <a href="#access-use">Access &amp; Use</a>
            <a href="#user-accounts">User Accounts</a>
            <a href="#role-management">Role Management</a>
            <a href="#prohibited">Prohibited Activities</a>
            <a href="#intellectual-property">Intellectual Property</a>
            <a href="#service-availability">Service Availability</a>
            <a href="#termination">Termination</a>
            <a href="#liability">Limitation of Liability</a>
            <a href="#governing-law">Governing Law</a>
            <a href="#changes">Changes to Terms</a>
          </nav>
        </aside>

        {/* Content cards */}
        <div className="terms-content">

          <div className="terms-section-card" id="definitions">
            <h2>1. Definitions</h2>
            <p><strong>Aisle Agent Store</strong> refers to the multi-tenant AI platform owned and operated by SLT for the creation and management of custom AI agents.</p>
            <p><strong>User</strong>, <strong>You</strong>, or <strong>Your</strong> refers to any individual accessing a company workspace or using agents within an authorized tenant.</p>
            <p><strong>Custom Agents</strong> are specialized AI tools created, configured, and managed by individual company administrators for their specific business needs.</p>
            <p><strong>Workspace</strong> refers to the isolated environment granted to a registered company for managing its users and agents.</p>
          </div>

          <div className="terms-section-card" id="access-use">
            <h2>2. Access and Use</h2>
            <p>Access to the platform is strictly restricted to registered company workspaces and their authorized members.</p>
            <ul>
              <li><strong>Workspace Registration</strong>: Companies must register and be approved by the system administrator to gain access.</li>
              <li><strong>Agent Management</strong>: Administrators within a workspace have the authority to create, configure, and delete custom AI agents.</li>
              <li><strong>User Access</strong>: Access to custom agents is granted based on the roles assigned by workspace administrators.</li>
              <li><strong>Resource Usage</strong>: Users must use the platform resources in compliance with these terms and their respective company policies.</li>
            </ul>
          </div>

          <div className="terms-section-card" id="user-accounts">
            <h2>3. User Accounts</h2>
            <p>Only users authenticated through Google sign-in may access private agents. Admin users may create, manage, and assign roles to new users. You are responsible for maintaining the confidentiality of your login credentials.</p>
          </div>

          <div className="terms-section-card" id="role-management">
            <h2>4. Role Management</h2>
            <p>The Admin dashboard provides tools for:</p>
            <ul>
              <li>User creation &amp; deletion</li>
              <li>Role assignment</li>
              <li>Displaying role descriptions and user counts</li>
            </ul>
          </div>

          <div className="terms-section-card" id="prohibited">
            <h2>5. Prohibited Activities</h2>
            <p>You agree not to:</p>
            <ul>
              <li>Misuse, reverse-engineer, or interfere with the platform</li>
              <li>Upload or share malicious content</li>
              <li>Impersonate any entity or user</li>
            </ul>
          </div>

          <div className="terms-section-card" id="intellectual-property">
            <h2>6. Intellectual Property</h2>
            <p>All AI agents, software, and content are the exclusive property of <strong>SLT</strong>. Unauthorized reproduction, distribution, or modification is strictly prohibited.</p>
          </div>

          <div className="terms-section-card" id="service-availability">
            <h2>7. Service Availability</h2>
            <p>SLT reserves the right to modify, disable, or discontinue any platform feature, service, or agent template at its discretion. Super Admin users are responsible for platform-level administration and oversight but do not have access to or control over company-specific agents, knowledge bases, configurations, or data unless explicitly authorized.</p>
          </div>

          <div className="terms-section-card" id="termination">
            <h2>8. Termination</h2>
            <p>SLT reserves the right to suspend or terminate your access in case of policy violations or misuse.</p>
          </div>

          <div className="terms-section-card" id="liability">
            <h2>9. Limitation of Liability</h2>
            <p>SLT is not liable for indirect, incidental, or consequential damages arising from the use of any AI agent or service.</p>
          </div>

          <div className="terms-section-card" id="governing-law">
            <h2>10. Governing Law</h2>
            <p>These Terms shall be governed and interpreted under the laws of <strong>Sri Lanka</strong>.</p>
          </div>

          <div className="terms-section-card" id="changes">
            <h2>11. Changes to Terms</h2>
            <p>We may update these Terms at any time. Continued use after changes indicates your acceptance.</p>
          </div>

        </div>
      </div>

      <Footer />
    </div>
  );
}

export default Term;
