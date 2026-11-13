import React from "react";
import { useNavigate } from "react-router-dom";
import "./TopHeader.css";

const TopHeader = ({
  leftTo = "/",
  rightTo = null,
  leftAlt = "AisleAI Agents Logo",
  rightAlt = "Embryo Logo",
  rightSrc = "/embryo-removebg.png",
}) => {
  const navigate = useNavigate();
  const leftClickable = true;
  const rightClickable = Boolean(rightTo);

  return (
    <div className="app-top-header">
      <img
        src="/aisleaiagents.png"
        alt={leftAlt}
        className={`app-top-header-logo ${leftClickable ? "clickable" : ""}`.trim()}
        onClick={leftClickable ? () => navigate("/") : undefined}
      />
      <img
        src={rightSrc}
        alt={rightAlt}
        className={`app-top-header-logo ${rightClickable ? "clickable" : ""}`.trim()}
        onClick={rightClickable ? () => navigate(rightTo) : undefined}
      />
    </div>
  );
};

export default TopHeader;
