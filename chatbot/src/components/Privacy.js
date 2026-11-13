import React from 'react';
import Footer from './Footer';
import Navbar from "./Navbar";
import './Privacy.css';

function Privacy() {
  const handleSectionNav = (event, sectionId) => {
    event.preventDefault();

    const target = document.getElementById(sectionId);
    if (!target) {
      return;
    }

    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  };

  return (
    <div className="privacy-page">
      <Navbar />

      {/* ── Hero Header ── */}
      <div className="privacy-hero">
        <div className="privacy-eyebrow">
          <span className="privacy-eyebrow-dot" />
          Legal
        </div>
        <h1>Privacy <span>Policy</span></h1>
        <p>Last Updated: June 20, 2025</p>
      </div>

      {/* ── Two-column layout: nav + content ── */}
      <div className="privacy-layout">

        {/* Sticky sidebar nav */}
        <nav className="privacy-nav">
          <div className="privacy-nav-title">Contents</div>
          <a href="#data-collected" onClick={(event) => handleSectionNav(event, 'data-collected')}>1. Data We Collect</a>
          <a href="#data-use" onClick={(event) => handleSectionNav(event, 'data-use')}>2. How We Use Your Data</a>
          <a href="#cookies-analytics" onClick={(event) => handleSectionNav(event, 'cookies-analytics')}>3. Cookies & Analytics</a>
          <a href="#data-sharing" onClick={(event) => handleSectionNav(event, 'data-sharing')}>4. Data Sharing</a>
          <a href="#security" onClick={(event) => handleSectionNav(event, 'security')}>5. Security Measures</a>
          <a href="#user-rights" onClick={(event) => handleSectionNav(event, 'user-rights')}>6. Your Rights</a>
          <a href="#data-retention" onClick={(event) => handleSectionNav(event, 'data-retention')}>7. Data Retention</a>
          <a href="#third-party" onClick={(event) => handleSectionNav(event, 'third-party')}>8. Third-Party Services</a>
          <a href="#child-privacy" onClick={(event) => handleSectionNav(event, 'child-privacy')}>9. Children's Privacy</a>
          <a href="#policy-changes" onClick={(event) => handleSectionNav(event, 'policy-changes')}>10. Policy Changes</a>
        </nav>

        {/* Content sections */}
        <main className="privacy-content">

          <section className="privacy-section" id="data-collected">
            <h2>Data We Collect</h2>
            <p>To provide a secure and customized experience, we collect only the necessary data required for workspace management and agent functionality.</p>
            <h3>a. Company & User Information</h3>
            <ul>
              <li>Company registration details (Name, Industry, Website, Domain)</li>
              <li>User authentication credentials via secure sign-in (Name, Email)</li>
              <li>Role-based metadata and administrative permissions</li>
            </ul>
            <h3>b. Custom Agent Configurations</h3>
            <ul>
              <li>Agent names and functional settings</li>
              <li>External tool configurations, including API endpoints and parameters</li>
              <li>Form builder fields and submission logic</li>
              <li>Knowledge Base sources, including provided URLs and OneDrive folder links</li>
              <li>Securely stored authentication tokens specifically for external resource access</li>
            </ul>
          </section>

          <section className="privacy-section" id="data-use">
            <h2>How We Use Your Data</h2>
            <ul>
              <li>Provide AI assistant functionality</li>
              <li>Personalize your experience</li>
              <li>Enhance security and monitor usage</li>
              <li>Improve AI agent accuracy and performance</li>
              <li>Administrative reporting (for Admin users)</li>
            </ul>
          </section>

          <section className="privacy-section" id="cookies-analytics">
            <h2>Cookies and Analytics</h2>
            <p>We may use cookies to:</p>
            <ul>
              <li>Maintain session state</li>
              <li>Track performance metrics</li>
              <li>Analyze engagement (e.g., agent usage frequency)</li>
            </ul>
          </section>

          <section className="privacy-section" id="data-sharing">
            <h2>Data Sharing &amp; Disclosure</h2>
            <div className="privacy-highlight">
              We do <strong>not</strong> sell, rent, or trade your personal data.
            </div>
            <p style={{ marginTop: '12px' }}>
              Data may be shared internally within <strong>SLT</strong> for system
              enhancement and technical support.
            </p>
            <p>Legal disclosures may occur when required by law.</p>
          </section>

          <section className="privacy-section" id="security">
            <h2>Security Measures</h2>
            <ul>
              <li>SSL encryption for all data transmissions</li>
              <li>Google OAuth for secure sign-in</li>
              <li>Role-based access control for system management</li>
              <li>Regular audits and monitoring</li>
            </ul>
          </section>

          <section className="privacy-section" id="user-rights">
            <h2>Your Rights</h2>
            <p>You have the right to:</p>
            <ul>
              <li>Request access to your data</li>
              <li>Correct inaccurate information</li>
             
            </ul>
          </section>

          <section className="privacy-section" id="data-retention">
            <h2>Data Retention</h2>
            <p>
              Data is retained only as long as necessary to provide services or
              fulfill legal obligations. User accounts and activity logs may be
              archived for security compliance.
            </p>
          </section>

          <section className="privacy-section" id="third-party">
            <h2>Third-Party Services</h2>
            <p>
              <strong>Google Sign-In</strong> is used for authentication. Your
              interaction with Google services is governed by Google's Privacy
              Policy.
            </p>
          </section>

          <section className="privacy-section" id="child-privacy">
            <h2>Children's Privacy</h2>
            <p>This system is not intended for users under the age of 16.</p>
          </section>

          <section className="privacy-section" id="policy-changes">
            <h2>Changes to Privacy Policy</h2>
            <p>
              We may revise this policy periodically. Updated versions will be
              posted on this page with an effective date.
            </p>
          </section>

        </main>
      </div>

      <Footer />
    </div>
  );
}

export default Privacy;