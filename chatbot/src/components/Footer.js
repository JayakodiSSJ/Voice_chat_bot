import React from 'react';
import './Footer.css';

const Footer = () => {
  return (
    <footer className="app-footer">
      <div className="footer-content">
        <div className="footer-text">
          <span className="powered-by">Powered By</span>

          <img
            src="/embryo-removebg.png"
            alt="Embryo Logo"
            className="footer-logo"
          />

        </div>

        <div className="footer-year">
          &copy; {new Date().getFullYear()}
        </div>

        <div className="footer-links">
          <a href="/terms" className="footer-link">Terms & Conditions</a>
          <a href="/privacy" className="footer-link">Privacy</a>
        </div>
      </div>
    </footer>
  );
};

export default Footer;