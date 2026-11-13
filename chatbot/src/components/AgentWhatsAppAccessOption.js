import React from "react";
import { FaWhatsapp } from "react-icons/fa";


const AgentWhatsAppAccessOption = ({
  visible,
  enabled,
  available,
  phoneNumber,
  onChange,
}) => {
  if (!visible) return null;

  const cannotEnable = !available && !enabled;
  return (
    <div className="agent-whatsapp-access">
      <p className="agent-whatsapp-access-title">Channel access</p>
      <label
        className={`tool-checkbox-row tool-checkbox-row--whatsapp ${enabled ? "tool-checkbox-row--active" : ""} ${cannotEnable ? "tool-checkbox-row--disabled" : ""}`}
        title={cannotEnable ? "Configure the workspace WhatsApp number first" : ""}
      >
        <input
          type="checkbox"
          checked={enabled}
          disabled={cannotEnable}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="tool-checkbox-custom" aria-hidden="true"></span>
        <div className="tool-checkbox-label-text">
          <strong><FaWhatsapp aria-hidden="true" /> WhatsApp access</strong>
          <span>Allow users messaging the company number to select this agent.</span>
        </div>
        <span className={`agent-whatsapp-access-status ${available ? "agent-whatsapp-access-status--ready" : ""}`}>
          {available ? phoneNumber : (enabled ? "Configuration removed" : "Number required")}
        </span>
      </label>
    </div>
  );
};

export default AgentWhatsAppAccessOption;
