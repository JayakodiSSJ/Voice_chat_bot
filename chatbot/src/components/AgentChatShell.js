import React from "react";
import { useNavigate } from "react-router-dom";
import BackArrow from "./BackArrow";
import Navbar from "./Navbar";
import Footer from "./Footer";
import "./AgentChatShell.css";

const AgentChatShell = ({
  sidebar,
  children,
}) => {
  const navigate = useNavigate();

  return (
    <div className="agent-chat-page">
      <Navbar />

      <div className="agent-chat-topbar">
        <BackArrow
          className="agent-chat-back-button"
          title="Back to dashboard"
          onClick={() => navigate("/dashboard")}
        />
      </div>

      <div className={`agent-chat-layout${sidebar ? " with-sidebar" : ""}`}>
        {sidebar}
        <div className="agent-chat-card">
          <div className="agent-chat-content">{children}</div>
        </div>
      </div>

      <Footer />
    </div>
  );
};

export default AgentChatShell;
