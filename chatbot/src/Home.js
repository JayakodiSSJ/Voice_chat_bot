import React from "react";
import { useNavigate } from "react-router-dom";
import "./css.css";
import TopHeader from "./components/TopHeader";

const Home = () => {
  const navigate = useNavigate();

  const handleAgentClick = (role) => {
    navigate("/login", { state: { role } });
  };

  return (
    <>
      <TopHeader leftTo="/" />

      <div className="container">
        <h1>Agent Store</h1>
        <div className="agents-grid">
          <AgentCard agentName="test-aisle-web" iconSrc="/bot.png" onClick={() => handleAgentClick("A")} />
          <AgentCard agentName="Innospace Assistant" iconSrc="/innospace_bot.png" onClick={() => handleAgentClick("B")} />
          <AgentCard agentName="SLT Digital Lab Internship Q&A" iconSrc="/digital_bot.png" onClick={() => handleAgentClick("C")} />
          <AgentCard agentName="SLT Embryo Agent" iconSrc="/embryo_bot.png" onClick={() => handleAgentClick("D")} />
          <AgentCard agentName="Internship Placement" iconSrc="/internship_bot.png" onClick={() => handleAgentClick("E")} />

          <AgentCard agentName="Aisle Agent for SLTMNIT" iconSrc="/sltmnit.png" onClick={() => handleAgentClick("G")} />
        </div>
      </div>
    </>
  );
};

const AgentCard = ({ agentName, iconSrc, onClick }) => (
  <div className="agent-card" onClick={onClick}>
    <img src={iconSrc} alt={`${agentName} Icon`} className="agent-icon" />
    <div className="agent-name">{agentName}</div>
  </div>
);

export default Home;
