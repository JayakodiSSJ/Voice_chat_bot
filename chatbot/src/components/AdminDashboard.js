import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  FaUserPlus,
  FaEdit,
  FaTrash,
  FaUsers,
  FaBuilding,
  FaCreditCard,
  FaLayerGroup,
  FaRobot,
  FaDatabase,
  FaSave,
  FaWhatsapp,
} from "react-icons/fa";
import "./AdminDashboard.css";
import Footer from "./Footer";
import { fetchMe, getToken } from "../auth/authClient";
import { URL } from "../urls";
import { PACKAGE_ORDER, limitInputValue, parseLimitInput } from "../utils/subscriptionPackages";
import Navbar from "./Navbar";
import BackArrow from "./BackArrow";
import LogoutButton from "./LogoutButton";
import { useMsal } from "@azure/msal-react";
import { loginRequest, tokenRequest } from "../authConfig";
import { useAppDialog } from "./AppDialog";

const AgentResourceCard = ({
  code,
  label,
  initialOneDrive,
  initialWebsite,
  initialAccessToken,
  onSave,
  onIngest,
  disabled,
  ingestionTask,
}) => {
  const { instance, accounts } = useMsal();
  const [onedrive, setOnedrive] = useState(initialOneDrive);
  const [website, setWebsite] = useState(initialWebsite);
  const [accessToken, setAccessToken] = useState(initialAccessToken);
  const [isModified, setIsModified] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    setOnedrive(initialOneDrive);
    setWebsite(initialWebsite);
    setAccessToken(initialAccessToken);
    setIsModified(false);
  }, [initialOneDrive, initialWebsite, initialAccessToken]);

  const handleChange = (setter) => (e) => {
    setter(e.target.value);
    setIsModified(true);
  };

  const handleMicrosoftLogin = async () => {
    setIsLoggingIn(true);
    try {
      // Force account selection so it doesn't auto-pick the wrong (SLIIT) account
      const loginResponse = await instance.loginPopup({
        ...loginRequest,
        prompt: "select_account"
      });
      instance.setActiveAccount(loginResponse.account);

      const tokenResponse = await instance.acquireTokenSilent({
        ...tokenRequest,
        account: loginResponse.account
      });

      setAccessToken(tokenResponse.accessToken);
      setIsModified(true);
    } catch (error) {
      console.error("Microsoft Login Error:", error);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const currentAccount = accounts[0];
  const isActiveTask = ingestionTask?.agent === code && ingestionTask?.status !== 'idle';

  return (
    <div className="resource-card">
      <div className="resource-card-header">
        <div className="resource-agent-badge">
          <span className="resource-agent-code">{code}</span>
          <span className="resource-agent-name">{label}</span>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {isActiveTask && (
            <span className={`status-pill ${ingestionTask.status}`}>
              {ingestionTask.status === 'running' && <span className="spinner-small" style={{ marginRight: '6px' }}></span>}
              {ingestionTask.status.toUpperCase()}
            </span>
          )}
          {isModified && (
            <span className="resource-unsaved-badge">Unsaved changes</span>
          )}
        </div>
      </div>

      <div className="resource-fields">
        {/* OneDrive Section */}
        <div className="resource-field-group">
          <label className="resource-field-label">
            <i className="fas fa-cloud" style={{ marginRight: "8px", color: "#58a6ff" }}></i>
            OneDrive Link / Folder ID
          </label>
          <div className="input-with-action">
            <input
              type="text"
              value={onedrive}
              onChange={handleChange(setOnedrive)}
              placeholder="Paste OneDrive Link or Folder Path..."
              className="resource-field-input"
              disabled={disabled}
            />
            <button
              className="inline-action-btn onedrive"
              title="Sync OneDrive"
              onClick={async () => {
                let freshToken = accessToken;
                let tokenSource = "stored_state";
                let lastTokenResponse = null;
                if (currentAccount) {
                  try {
                    const tokenResponse = await instance.acquireTokenSilent({
                      ...tokenRequest,
                      account: currentAccount
                    });
                    lastTokenResponse = tokenResponse;
                    freshToken = tokenResponse.accessToken;
                    tokenSource = "msal_silent";
                    setAccessToken(freshToken);
                    onSave(code, onedrive, website, freshToken);
                  } catch (e) {
                    console.warn("Silent token refresh failed, trying interactive popup:", e);
                    try {
                      const tokenResponse = await instance.acquireTokenPopup({
                        ...tokenRequest,
                        account: currentAccount,
                      });
                      lastTokenResponse = tokenResponse;
                      freshToken = tokenResponse.accessToken;
                      tokenSource = "msal_popup";
                      setAccessToken(freshToken);
                      onSave(code, onedrive, website, freshToken);
                    } catch (e2) {
                      console.error("Interactive token acquisition failed:", e2);
                    }
                  }
                } else {
                  console.warn("No active MSAL account — using stored token, which may be stale");
                }
                console.log(
                  "[Sync] token source:", tokenSource,
                  "len:", freshToken ? freshToken.length : 0,
                  "dots:", freshToken ? (freshToken.match(/\./g) || []).length : 0,
                  "first40:", freshToken ? freshToken.slice(0, 40) : null,
                  "last20:", freshToken ? freshToken.slice(-20) : null,
                  "scopes:", lastTokenResponse?.scopes,
                  "tokenType:", lastTokenResponse?.tokenType,
                  "account_env:", lastTokenResponse?.account?.environment,
                  "account_tenantId:", lastTokenResponse?.account?.tenantId,
                );
                onIngest(code, 'onedrive', onedrive, freshToken);
              }}
              disabled={disabled}
            >
              <i className="fas fa-sync-alt"></i>
            </button>
          </div>
        </div>

        {/* Website Section */}
        <div className="resource-field-group">
          <label className="resource-field-label">
            <i className="fas fa-globe" style={{ marginRight: "8px", color: "#4ade80" }}></i>
            Website Link
          </label>
          <div className="input-with-action">
            <input
              type="text"
              value={website}
              onChange={handleChange(setWebsite)}
              placeholder="e.g. http://localhost:5000 or https://www.example.com"
              className="resource-field-input"
              disabled={disabled}
            />
            <button
              className="inline-action-btn website"
              title="Sync Website"
              onClick={() => onIngest(code, 'url', website)}
              disabled={disabled}
            >
              <i className="fas fa-sync-alt"></i>
            </button>
          </div>
        </div>

        {/* Access Token Section */}
        <div className="resource-field-group">
          <label className="resource-field-label">
            <i className="fas fa-key" style={{ marginRight: "8px", color: "#fbbf24" }}></i>
            Graph API Key (OneDrive)
          </label>
          <div className="auth-field-row">
            <div className="input-with-action flex-grow" style={{ position: 'relative' }}>
              <input
                type={showToken ? "text" : "password"}
                value={accessToken}
                onChange={handleChange(setAccessToken)}
                placeholder="Graph API key will auto-fill..."
                className="resource-field-input"
                disabled={disabled}
                style={{ paddingRight: '45px' }}
              />
              <button
                className="token-toggle-btn"
                type="button"
                onClick={() => setShowToken(!showToken)}
                title={showToken ? "Hide Graph API Key" : "Show Graph API Key"}
                style={{
                  position: 'absolute',
                  right: '10px',
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'none',
                  border: 'none',
                  color: 'rgba(162, 40, 255, 0.6)',
                  cursor: 'pointer',
                  zIndex: 10
                }}
              >
                <i className={`fas ${showToken ? 'fa-eye-slash' : 'fa-eye'}`}></i>
              </button>
            </div>
            <button
              className="ms-login-btn"
              onClick={handleMicrosoftLogin}
              disabled={disabled || isLoggingIn}
            >
              <i className="fab fa-microsoft"></i>
              {isLoggingIn ? "Wait..." : "Login with MS"}
            </button>
          </div>
          {/* Show the account associated with the current session */}
          {(instance.getActiveAccount() || (accounts.length > 0 ? accounts[0] : null)) && (
            <div className="account-info">
              <i className="fas fa-check-circle"></i>
              Connected as: <span>{(instance.getActiveAccount() || accounts[0]).username}</span>
            </div>
          )}
        </div>
      </div>

      <div className="resource-card-footer">
        <button
          onClick={() => {
            onSave(code, onedrive, website, accessToken);
            setIsModified(false);
          }}
          className={`resource-save-btn ${!isModified ? "disabled" : ""}`}
          disabled={disabled || !isModified}
        >
          {disabled ? (
            <>
              <span className="spinner-small"></span> Processing...
            </>
          ) : (
            <>
              <i className="fas fa-save" style={{ marginRight: "6px" }}></i>
              Save Configuration
            </>
          )}
        </button>
      </div>
    </div>
  );
};

const UNLIMITED_LIMIT_FIELDS = new Set([
  "agent_limit",
]);

const PackageConfigCard = ({ packageId, config, onChange }) => {
  const updateField = (field, value) => {
    onChange(packageId, field, value);
  };

  const updateLimitField = (field, value) => {
    updateField(field, UNLIMITED_LIMIT_FIELDS.has(field) ? parseLimitInput(value) : value);
  };

  return (
    <div className="package-config-card">
      <div className="package-config-card-header">
        <h3>{config.name || packageId}</h3>
        <label className="package-popular-toggle">
          <input
            type="checkbox"
            checked={Boolean(config.popular)}
            onChange={(e) => updateField("popular", e.target.checked)}
          />
          Most Popular
        </label>
      </div>

      <div className="package-config-grid">
        <label>
          Display Name
          <input
            type="text"
            value={config.name || ""}
            onChange={(e) => updateField("name", e.target.value)}
          />
        </label>
        <label>
          Monthly Price (USD)
          <input
            type="number"
            min="0"
            step="0.01"
            value={config.price_monthly_usd ?? 0}
            onChange={(e) => updateField("price_monthly_usd", Number(e.target.value))}
          />
        </label>
        <label className="package-config-span-2">
          Description
          <textarea
            rows={2}
            value={config.description || ""}
            onChange={(e) => updateField("description", e.target.value)}
          />
        </label>
        <label>
          Agents
          <input
            type="text"
            placeholder="Unlimited"
            value={limitInputValue(config.agent_limit)}
            onChange={(e) => updateLimitField("agent_limit", e.target.value)}
          />
        </label>
        <label>
          Monthly Tokens
          <input
            type="number"
            min="0"
            value={config.monthly_tokens ?? 0}
            onChange={(e) => updateField("monthly_tokens", Number(e.target.value))}
          />
        </label>
        <label>
          Agent Expiry (Days)
          <input
            type="number"
            min="1"
            value={config.agent_expiry_days ?? 30}
            onChange={(e) => updateField("agent_expiry_days", Number(e.target.value))}
          />
        </label>
      </div>

      <div className="package-config-toggles">
        {(() => {
          const isKbChecked = Boolean(config.allow_website_kb || config.allow_onedrive_kb || config.allow_sharepoint_kb);
          return (
            <>
              <div className="package-toggle-row">
                <label className={`package-toggle-pill ${isKbChecked ? "is-active" : ""}`}>
                  <input 
                    type="checkbox" 
                    checked={isKbChecked} 
                    onChange={(e) => {
                      const checked = e.target.checked;
                      if (checked) {
                        updateField("allow_website_kb", true);
                      } else {
                        updateField("allow_website_kb", false);
                        updateField("allow_onedrive_kb", false);
                        updateField("allow_sharepoint_kb", false);
                      }
                    }} 
                  />
                  <span className="package-toggle-check" aria-hidden="true"></span>
                  <span className="package-toggle-text">
                    <strong>KB</strong>
                    <small>Knowledge base sources</small>
                  </span>
                </label>
                
                <label className={`package-toggle-pill ${config.allow_form_builder ? "is-active" : ""}`}>
                  <input 
                    type="checkbox" 
                    checked={Boolean(config.allow_form_builder)} 
                    onChange={(e) => updateField("allow_form_builder", e.target.checked)} 
                  />
                  <span className="package-toggle-check" aria-hidden="true"></span>
                  <span className="package-toggle-text">
                    <strong>Form</strong>
                    <small>Conversational forms</small>
                  </span>
                </label>

                <label className={`package-toggle-pill ${config.allow_endpoint_tools ? "is-active" : ""}`}>
                  <input 
                    type="checkbox" 
                    checked={Boolean(config.allow_endpoint_tools)} 
                    onChange={(e) => updateField("allow_endpoint_tools", e.target.checked)} 
                  />
                  <span className="package-toggle-check" aria-hidden="true"></span>
                  <span className="package-toggle-text">
                    <strong>Endpoint</strong>
                    <small>REST API tools</small>
                  </span>
                </label>

                <label className={`package-toggle-pill ${config.allow_mcp ? "is-active" : ""}`}>
                  <input 
                    type="checkbox" 
                    checked={Boolean(config.allow_mcp)} 
                    onChange={(e) => updateField("allow_mcp", e.target.checked)} 
                  />
                  <span className="package-toggle-check" aria-hidden="true"></span>
                  <span className="package-toggle-text">
                    <strong>MCP</strong>
                    <small>Protocol servers</small>
                  </span>
                </label>
              </div>

              {isKbChecked && (
                <div className="package-kb-source-panel">
                  <label className={`package-source-chip ${config.allow_website_kb ? "is-active" : ""}`}>
                    <input 
                      type="checkbox" 
                      checked={Boolean(config.allow_website_kb)} 
                      onChange={(e) => updateField("allow_website_kb", e.target.checked)} 
                    />
                    <span>Website URL</span>
                  </label>
                  <label className={`package-source-chip ${config.allow_onedrive_kb ? "is-active" : ""}`}>
                    <input 
                      type="checkbox" 
                      checked={Boolean(config.allow_onedrive_kb)} 
                      onChange={(e) => updateField("allow_onedrive_kb", e.target.checked)} 
                    />
                    <span>OneDrive</span>
                  </label>
                  <label className={`package-source-chip ${config.allow_sharepoint_kb ? "is-active" : ""}`}>
                    <input 
                      type="checkbox" 
                      checked={Boolean(config.allow_sharepoint_kb)} 
                      onChange={(e) => updateField("allow_sharepoint_kb", e.target.checked)} 
                    />
                    <span>SharePoint</span>
                  </label>
                </div>
              )}
            </>
          );
        })()}
      </div>
    </div>
  );
};

const AdminDashboard = () => {
  const navigate = useNavigate();
  const { instance } = useMsal();
  const { showAlert, showConfirm, showPrompt } = useAppDialog();
  const [users, setUsers] = useState([]);
  const [companyRegistrations, setCompanyRegistrations] = useState([]);
  const [companyAgentRows, setCompanyAgentRows] = useState([]);
  const [pendingCompanyRegistrations, setPendingCompanyRegistrations] =
    useState([]);
  const [
    pendingCompanyRegistrationsCount,
    setPendingCompanyRegistrationsCount,
  ] = useState(0);

  const [companyAllowedAgents, setCompanyAllowedAgents] = useState([]);
  const [newUser, setNewUser] = useState({
    email: "",
    fullName: "",
    role: "",
  });
  const [editingUser, setEditingUser] = useState(null);
  const [editingUserData, setEditingUserData] = useState({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState({ text: "", type: "" });
  const [showAddUserPopup, setShowAddUserPopup] = useState(false);
  const [showUserManagementPopup, setShowUserManagementPopup] = useState(false);
  const [
    showPendingCompanyRegistrationsPopup,
    setShowPendingCompanyRegistrationsPopup,
  ] = useState(false);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [adminPayments, setAdminPayments] = useState([]);
  const [showPaymentsPopup, setShowPaymentsPopup] = useState(false);
  const [companyInfo, setCompanyInfo] = useState(null);
  const [currentUserInfo, setCurrentUserInfo] = useState(null);
  const [showResetStep1, setShowResetStep1] = useState(false);
  const [showResetStep2, setShowResetStep2] = useState(false);
  const [resetConfirmationText, setResetConfirmationText] = useState("");
  const [isResetting, setIsResetting] = useState(false);
  const [customAgents, setCustomAgents] = useState([]);
  const [showAgentsCreatedPopup, setShowAgentsCreatedPopup] = useState(false);
  const [showPackagesPopup, setShowPackagesPopup] = useState(false);
  const [packageConfigDraft, setPackageConfigDraft] = useState({});
  const [packagesUpdatedAt, setPackagesUpdatedAt] = useState(null);
  const [savingPackages, setSavingPackages] = useState(false);
  const [showWhatsappAddonPopup, setShowWhatsappAddonPopup] = useState(false);
  const [whatsappAddonDraft, setWhatsappAddonDraft] = useState({
    monthly_price_usd: "",
    available: false,
  });
  const [whatsappAddonUpdatedAt, setWhatsappAddonUpdatedAt] = useState(null);
  const [whatsappAddonLoading, setWhatsappAddonLoading] = useState(false);
  const [savingWhatsappAddon, setSavingWhatsappAddon] = useState(false);
  const [whatsappAddonError, setWhatsappAddonError] = useState("");

  const [showAgentResourcesPopup, setShowAgentResourcesPopup] = useState(false);
  const [agentResources, setAgentResources] = useState([]);
  const [selectedAgentForResources, setSelectedAgentForResources] = useState("");
  const [ingestionTask, setIngestionTask] = useState({ status: 'idle' });
  const [showCompanyAgentsPopup, setShowCompanyAgentsPopup] = useState(false);
  const [selectedCompanyForAgents, setSelectedCompanyForAgents] = useState(null);
  const [companySearch, setCompanySearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [userSearch, setUserSearch] = useState("");
  const [chatUsage, setChatUsage] = useState(null);
  const [chatUsageLoading, setChatUsageLoading] = useState(false);
  const [savingUserQuotaId, setSavingUserQuotaId] = useState(null);
  const [databaseToolAudit, setDatabaseToolAudit] = useState([]);
  const [databaseToolAuditLoading, setDatabaseToolAuditLoading] = useState(false);
  const [showDatabaseAuditPopup, setShowDatabaseAuditPopup] = useState(false);
  const [databaseAuditUserFilter, setDatabaseAuditUserFilter] = useState("");
  const [databaseAuditDateFilter, setDatabaseAuditDateFilter] = useState("");
  const [databaseAuditAppliedFilters, setDatabaseAuditAppliedFilters] = useState({ user: "", date: "" });

  // Member invitation
  const [newMember, setNewMember] = useState({ full_name: '', email: '' });
  const [pendingMemberInvitations, setPendingMemberInvitations] = useState([]);
  const [addingMember, setAddingMember] = useState(false);

  const getAuthHeaders = useCallback(() => {
    const token = getToken();
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  }, []);

  const showMessage = useCallback((text, type) => {
    setMessage({ text, type });
    setTimeout(() => setMessage({ text: "", type: "" }), 5000);
  }, []);


  const toggleAddUserPopup = () => {
    setShowAddUserPopup((prevState) => !prevState);
  };

  const toggleUserManagementPopup = () => {
    setShowUserManagementPopup((prevState) => !prevState);
  };

  const togglePendingCompanyRegistrationsPopup = async () => {
    const willOpen = !showPendingCompanyRegistrationsPopup;
    setShowPendingCompanyRegistrationsPopup(willOpen);
    if (willOpen) {
      await loadPendingCompanyRegistrations();
    }
  };

  const toggleAgentResourcesPopup = () => {
    setShowAgentResourcesPopup(!showAgentResourcesPopup);
  };

  const togglePaymentsPopup = async () => {
    const willOpen = !showPaymentsPopup;
    setShowPaymentsPopup(willOpen);
    if (willOpen) {
      await loadAdminPayments();
    }
  };

  const loadSubscriptionPackages = async () => {
    setLoading(true);
    try {
      const response = await fetch(URL.ADMIN_SUBSCRIPTION_PACKAGES, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch subscription packages");
      const data = await response.json();
      const draft = {};
      PACKAGE_ORDER.forEach((packageId) => {
        draft[packageId] = { ...(data.config?.[packageId] || {}) };
      });
      setPackageConfigDraft(draft);
      setPackagesUpdatedAt(data.updated_at || null);
    } catch (error) {
      console.error("Error loading subscription packages:", error);
      showMessage("Error loading subscription packages", "error");
    } finally {
      setLoading(false);
    }
  };

  const updatePackageConfigField = (packageId, field, value) => {
    setPackageConfigDraft((prev) => ({
      ...prev,
      [packageId]: {
        ...(prev[packageId] || {}),
        [field]: value,
      },
    }));
  };

  const saveSubscriptionPackages = async () => {
    setSavingPackages(true);
    try {
      const response = await fetch(URL.ADMIN_SUBSCRIPTION_PACKAGES, {
        method: "PUT",
        headers: getAuthHeaders(),
        body: JSON.stringify({ packages: packageConfigDraft }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || data.error || "Failed to save subscription packages");
      }
      const draft = {};
      PACKAGE_ORDER.forEach((packageId) => {
        draft[packageId] = { ...(data.config?.[packageId] || {}) };
      });
      setPackageConfigDraft(draft);
      setPackagesUpdatedAt(data.updated_at || new Date().toISOString());
      await showAlert("Subscription packages updated successfully.", {
        title: "Packages updated",
        variant: "success",
      });
    } catch (error) {
      console.error("Error saving subscription packages:", error);
      showMessage(error.message || "Error saving subscription packages", "error");
    } finally {
      setSavingPackages(false);
    }
  };

  const togglePackagesPopup = async () => {
    const willOpen = !showPackagesPopup;
    setShowPackagesPopup(willOpen);
    if (willOpen) {
      await loadSubscriptionPackages();
    }
  };

  const loadWhatsappAddonConfig = async () => {
    setWhatsappAddonLoading(true);
    setWhatsappAddonError("");
    try {
      const response = await fetch(URL.ADMIN_WHATSAPP_ADDON, {
        headers: getAuthHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || data.error || "Failed to load WhatsApp add-on configuration");
      }
      const config = data.config || {};
      setWhatsappAddonDraft({
        monthly_price_usd: String(config.monthly_price_usd ?? ""),
        available: Boolean(config.available),
      });
      setWhatsappAddonUpdatedAt(config.updated_at || null);
    } catch (error) {
      console.error("Error loading WhatsApp add-on configuration:", error);
      setWhatsappAddonError(error.message || "Failed to load WhatsApp add-on configuration");
    } finally {
      setWhatsappAddonLoading(false);
    }
  };

  const toggleWhatsappAddonPopup = async () => {
    const willOpen = !showWhatsappAddonPopup;
    setShowWhatsappAddonPopup(willOpen);
    setWhatsappAddonError("");
    if (willOpen) {
      await loadWhatsappAddonConfig();
    }
  };

  const saveWhatsappAddonConfig = async (event) => {
    event.preventDefault();
    setWhatsappAddonError("");

    const price = Number(whatsappAddonDraft.monthly_price_usd);
    const priceCents = Math.round(price * 100);
    if (
      whatsappAddonDraft.monthly_price_usd === "" ||
      !Number.isFinite(price) ||
      price < 0 ||
      !Number.isSafeInteger(priceCents) ||
      Math.abs((price * 100) - priceCents) > 0.000001
    ) {
      setWhatsappAddonError("Enter a valid non-negative monthly price with no more than two decimal places.");
      return;
    }
    if (whatsappAddonDraft.available && priceCents === 0) {
      setWhatsappAddonError("An available add-on requires a price greater than zero.");
      return;
    }

    setSavingWhatsappAddon(true);
    try {
      const response = await fetch(URL.ADMIN_WHATSAPP_ADDON, {
        method: "PUT",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          monthly_price_cents: priceCents,
          available: Boolean(whatsappAddonDraft.available),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || data.error || "Failed to save WhatsApp add-on configuration");
      }
      const config = data.config || {};
      setWhatsappAddonDraft({
        monthly_price_usd: String(config.monthly_price_usd ?? ""),
        available: Boolean(config.available),
      });
      setWhatsappAddonUpdatedAt(config.updated_at || new Date().toISOString());
      const priceSyncPending = ["pending", "partial"].includes(data.price_sync?.status);
      showMessage(
        data.message || "WhatsApp add-on configuration updated",
        priceSyncPending ? "info" : "success"
      );
    } catch (error) {
      console.error("Error saving WhatsApp add-on configuration:", error);
      setWhatsappAddonError(error.message || "Failed to save WhatsApp add-on configuration");
    } finally {
      setSavingWhatsappAddon(false);
    }
  };

  const toggleCompanyAgentsPopup = (company = null) => {
    setSelectedCompanyForAgents(company);
    setShowCompanyAgentsPopup(!!company);
  };

  useEffect(() => {
    document.body.classList.remove("admin-body-lock");
    document.documentElement.classList.remove("admin-body-lock");
  }, []);

  useEffect(() => {
    const loadDashboard = async () => {
      await Promise.all([
        loadUsers(),
        loadPendingMemberInvitations(),
        loadCustomAgents(),
        loadChatUsage(),
        loadDatabaseToolAudit(),
      ]);
      await loadAuthContext();
    };
    loadDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (currentUserInfo?.email) {
      const activeAccount = instance.getActiveAccount();
      if (!activeAccount || activeAccount.username.toLowerCase() !== currentUserInfo.email.toLowerCase()) {
        const account = instance.getAllAccounts().find(
          (acc) => acc.username.toLowerCase() === currentUserInfo.email.toLowerCase()
        );
        if (account) {
          instance.setActiveAccount(account);
        }
      }
    }
  }, [currentUserInfo, instance]);


  const loadUsers = async () => {
    setLoading(true);
    try {
      const response = await fetch(URL.AUTH_USERS, {
        headers: getAuthHeaders(),
      });

      if (!response.ok) throw new Error("Failed to fetch users");

      const usersList = await response.json();

      // Sort users by creation date (newest first) or alphabetically by name
      usersList.sort((a, b) => {
        const aName = (a.full_name || "").trim();
        const bName = (b.full_name || "").trim();
        return aName.localeCompare(bName);
      });

      setUsers(usersList);
    } catch (error) {
      console.error("Error loading users:", error);
      setMessage({ text: "Error loading users", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  const loadPendingMemberInvitations = async () => {
    try {
      const response = await fetch(URL.AUTH_USER_INVITATIONS, {
        headers: getAuthHeaders(),
      });
      if (response.status === 403) {
        setPendingMemberInvitations([]);
        return;
      }
      if (!response.ok) throw new Error("Failed to fetch pending member invitations");
      const invitations = await response.json();
      setPendingMemberInvitations(invitations);
    } catch (error) {
      console.error("Error loading pending member invitations:", error);
      setPendingMemberInvitations([]);
    }
  };

  const formatInviteTime = (value) => {
    if (!value) return "-";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString();
  };

  const inviteWorkspaceMember = async () => {
    const name = (newMember.full_name || '').trim();
    const email = (newMember.email || '').trim().toLowerCase();
    if (!name || name.length < 2) { showMessage('Full name must be at least 2 characters.', 'error'); return; }
    if (!email || !email.includes('@')) { showMessage('Please enter a valid email address.', 'error'); return; }
    setAddingMember(true);
    try {
      const response = await fetch(URL.AUTH_USERS, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify({ full_name: name, email, role: 'member' }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Failed to add member');
      setPendingMemberInvitations((current) => {
        const nextInvite = {
          id: data.id || `${email}-${Date.now()}`,
          full_name: data.full_name || name,
          email: data.email || email,
          invited_at: data.invited_at || new Date().toISOString(),
          status: data.status || "pending",
        };
        const withoutDuplicate = current.filter(
          (invite) => String(invite.email || "").toLowerCase() !== email
        );
        return [nextInvite, ...withoutDuplicate];
      });
      setNewMember({ full_name: '', email: '' });
      showMessage(`Invitation email sent to ${email} successfully.`, 'success');
      await loadPendingMemberInvitations();
    } catch (err) {
      showMessage(err.message || 'Error inviting member.', 'error');
    } finally {
      setAddingMember(false);
    }
  };

  const loadChatUsage = async () => {
    setChatUsageLoading(true);
    try {
      const response = await fetch(URL.CHAT_USAGE, {
        headers: getAuthHeaders(),
      });
      if (response.status === 403) {
        setChatUsage(null);
        return;
      }
      if (!response.ok) throw new Error("Failed to fetch chat usage");
      const data = await response.json();
      setChatUsage(data);
    } catch (error) {
      console.error("Error loading chat usage:", error);
    } finally {
      setChatUsageLoading(false);
    }
  };

  const loadDatabaseToolAudit = async (filters = {}) => {
    const user = String(filters.user ?? databaseAuditUserFilter).trim();
    const date = String(filters.date ?? databaseAuditDateFilter).trim();
    setDatabaseToolAuditLoading(true);
    try {
      const params = new URLSearchParams();
      if (user) params.set("user", user);
      if (date) params.set("date", date);
      const url = params.toString() ? `${URL.DATABASE_TOOL_AUDIT}?${params.toString()}` : URL.DATABASE_TOOL_AUDIT;
      const response = await fetch(url, {
        headers: getAuthHeaders(),
      });
      if (response.status === 403) {
        setDatabaseToolAudit([]);
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Failed to fetch database audit logs");
      setDatabaseToolAudit(Array.isArray(data.logs) ? data.logs : []);
      setDatabaseAuditAppliedFilters({ user, date });
    } catch (error) {
      console.error("Error loading database tool audit:", error);
      setDatabaseToolAudit([]);
      setDatabaseAuditAppliedFilters({ user, date });
    } finally {
      setDatabaseToolAuditLoading(false);
    }
  };

  const clearDatabaseAuditFilters = async () => {
    setDatabaseAuditUserFilter("");
    setDatabaseAuditDateFilter("");
    await loadDatabaseToolAudit({ user: "", date: "" });
  };

  const setUserChatQuota = async (usageUser) => {
    const current = usageUser.user_monthly_token_limit ?? "";
    const value = await showPrompt(
      "Set the maximum number of tokens this user can use each month. Leave the field blank to remove the custom cap.",
      {
        title: "Monthly token limit",
        inputLabel: "Token limit",
        inputType: "number",
        inputMode: "numeric",
        min: "0",
        defaultValue: current,
        placeholder: "No custom cap",
        confirmLabel: "Save limit",
      }
    );
    if (value === null) return;
    const trimmed = value.trim();
    if (trimmed && (!/^\d+$/.test(trimmed) || Number(trimmed) < 0)) {
      showMessage("Enter a valid non-negative monthly limit.", "error");
      return;
    }
    setSavingUserQuotaId(usageUser.user_id);
    try {
      const response = await fetch(URL.USER_CHAT_QUOTA(usageUser.user_id), {
        method: "PATCH",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          monthly_token_limit: trimmed ? Number(trimmed) : null,
          is_suspended: false,
          reason: trimmed ? "Admin-set user chat limit" : "",
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Failed to update user chat limit");
      showMessage("User chat limit updated.", "success");
      await loadChatUsage();
    } catch (error) {
      showMessage(error.message || "Failed to update user chat limit", "error");
    } finally {
      setSavingUserQuotaId(null);
    }
  };

  const clearUserChatQuota = async (usageUser) => {
    const confirmed = await showConfirm(
      `Clear the custom chat limit for ${usageUser.email}? The user will return to the workspace default limit.`,
      {
        title: "Clear custom limit",
        confirmLabel: "Clear limit",
      }
    );
    if (!confirmed) return;
    setSavingUserQuotaId(usageUser.user_id);
    try {
      const response = await fetch(URL.USER_CHAT_QUOTA(usageUser.user_id), {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || "Failed to clear user chat limit");
      showMessage("User chat limit cleared.", "success");
      await loadChatUsage();
    } catch (error) {
      showMessage(error.message || "Failed to clear user chat limit", "error");
    } finally {
      setSavingUserQuotaId(null);
    }
  };

  const loadCustomAgents = async () => {
    try {
      const response = await fetch(URL.AGENT_CONFIGURATIONS, {
        headers: getAuthHeaders(),
      });
      if (response.ok) {
        const data = await response.json();
        setCustomAgents(Array.isArray(data) ? data : []);
      }
    } catch (error) {
      console.error("Error loading custom agents:", error);
    }
  };

  const handleExtendSubscription = async () => {
    if (!companyInfo || !companyInfo.subscription_tier) return;
    const tier = companyInfo.subscription_tier;
    if (tier === 'free') {
      await showAlert("The Free tier cannot be extended. Upgrade to a premium plan from the dashboard instead.", {
        title: "Upgrade required",
        variant: "warning",
      });
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/auth/create-checkout-session", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders()
        },
        body: JSON.stringify({
          email: currentUserInfo?.email || "",
          tier: tier
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.url) {
        window.location.href = data.url;
      } else {
        throw new Error(data.message || data.error || "Failed to create checkout session");
      }
    } catch (err) {
      console.error("Extend subscription error:", err);
      await showAlert(err.message || "Failed to redirect to Stripe sandbox.", {
        title: "Unable to continue",
        variant: "danger",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleCancelSubscription = async () => {
    const confirmed = await showConfirm(
      "Your workspace will downgrade to the Free tier immediately and premium features will no longer be available.",
      {
        title: "Cancel subscription?",
        confirmLabel: "Cancel subscription",
        variant: "danger",
      }
    );
    if (!confirmed) return;
    setLoading(true);
    try {
      const res = await fetch("/api/auth/cancel-subscription", {
        method: "POST",
        headers: getAuthHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        await showAlert(data.message || "Subscription cancelled successfully.", {
          title: "Subscription cancelled",
          variant: "success",
        });
        window.location.reload();
      } else {
        throw new Error(data.message || data.error || "Failed to cancel subscription");
      }
    } catch (err) {
      console.error("Cancel subscription error:", err);
      await showAlert(err.message || "Failed to cancel subscription.", {
        title: "Cancellation failed",
        variant: "danger",
      });
    } finally {
      setLoading(false);
    }
  };


  const loadAgentResources = async () => {
    setLoading(true);
    try {
      const response = await fetch(URL.AGENT_RESOURCES, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch agent resources");
      const data = await response.json();
      setAgentResources(data);
    } catch (error) {
      console.error("Error loading agent resources:", error);
      showMessage("Error loading agent resources", "error");
    } finally {
      setLoading(false);
    }
  };


  const handleSaveAgentResource = async (agentCode, onedrive, website, accessToken) => {
    setLoading(true);
    try {
      const response = await fetch(URL.AGENT_RESOURCES, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          agent_code: agentCode,
          onedrive_link: onedrive,
          website_link: website,
          access_token: accessToken,
        }),
      });
      if (!response.ok) throw new Error("Failed to save agent resource");
      await showAlert("Configuration updated successfully.", {
        title: "Configuration saved",
        variant: "success",
      });
      await loadAgentResources();
    } catch (error) {
      console.error("Error saving resource:", error);
      showMessage("Error saving configuration", "error");
    } finally {
      setLoading(false);
    }
  };

  const handleIngest = async (agentCode, type, value, freshAccessToken) => {
    if (!value) {
      showMessage(`Please provide a ${type === 'url' ? 'website link' : 'OneDrive link'} first.`, "info");
      return;
    }
    setLoading(true);
    try {
      const endpoint = type === 'url' ? URL.INGEST_URL : URL.INGEST_ONEDRIVE;
      const body = { agent_code: agentCode };
      if (type === 'url') {
        body.url = value;
      } else {
        body.folder_link = value;
        // Forward fresh MS Graph token so the backend doesn't have to fall back
        // on the (likely-expired) stored copy.
        if (freshAccessToken) body.access_token = freshAccessToken;
      }

      const response = await fetch(endpoint, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error("Failed to trigger ingestion");

      // Start polling
      setIngestionTask({ status: 'running', agent: agentCode, source: type });
      showMessage(`${type === 'url' ? 'Website' : 'OneDrive'} ingestion started...`, "success");
    } catch (error) {
      console.error("Ingestion error:", error);
      showMessage("Failed to start ingestion process", "error");
    } finally {
      setLoading(false);
    }
  };


  useEffect(() => {
    const checkIngestionStatus = async () => {
      try {
        const agentCode = ingestionTask.agent;
        const url = agentCode
          ? `${URL.INGESTION_STATUS}?agent_name=${encodeURIComponent(agentCode)}`
          : URL.INGESTION_STATUS;
        const response = await fetch(url, {
          headers: getAuthHeaders(),
        });
        if (!response.ok) return;
        const data = await response.json();
        setIngestionTask(data);

        if (data.status === 'completed') {
          showMessage("Ingestion completed successfully!", "success");
        } else if (data.status === 'failed') {
          showMessage(`Ingestion failed: ${data.error || 'Unknown error'}`, "error");
        }
      } catch (e) {
        console.error("Status check error:", e);
      }
    };

    let interval;
    if (ingestionTask.status === 'running') {
      interval = setInterval(checkIngestionStatus, 3000);
    }
    return () => clearInterval(interval);
  }, [ingestionTask.status, ingestionTask.agent, showMessage, getAuthHeaders, setIngestionTask]);

  const loadAuthContext = async () => {
    try {
      const me = await fetchMe();
      const superAdmin = me?.user?.is_super_admin || false;
      setIsSuperAdmin(superAdmin);
      setCompanyInfo(me?.company || null);
      setCurrentUserInfo(me?.user || null);

      if (me?.company?.allowed_agents) {
        try {
          setCompanyAllowedAgents(JSON.parse(me.company.allowed_agents));
        } catch (e) {
          setCompanyAllowedAgents([]);
        }
      }

      if (superAdmin) {
        await Promise.all([
          loadCompanyAgents(),
          loadCompanyRegistrations(),
          loadPendingCompanyRegistrations(),
          loadAdminPayments(),
        ]);
      }
    } catch (error) {
      console.error("Error loading auth context:", error);
      setIsSuperAdmin(false);
    }
  };

  const handleResetClick = () => {
    setShowResetStep1(true);
  };

  const handleResetStep1Confirm = () => {
    setShowResetStep1(false);
    setShowResetStep2(true);
  };

  const handleResetCancel = () => {
    setShowResetStep1(false);
    setShowResetStep2(false);
    setResetConfirmationText("");
  };

  const handleResetFinalConfirm = async () => {
    if (resetConfirmationText !== "RESET") return;

    setIsResetting(true);
    setMessage({ text: "", type: "" });

    try {
      const response = await fetch(URL.RESET_DATABASE, {
        method: "POST",
        headers: getAuthHeaders(),
      });

      const data = await response.json().catch(() => ({}));

      if (response.ok) {
        setMessage({ text: "Database reset completed successfully. Returning to clean state.", type: "success" });
        setShowResetStep2(false);
        setResetConfirmationText("");
        // Reload dashboard
        const loadDashboard = async () => {
          await Promise.all([
            loadUsers(),
            loadPendingMemberInvitations(),
            loadCustomAgents(),
            loadChatUsage(),
            loadCompanyRegistrations(),
            loadPendingCompanyRegistrations(),
            loadCompanyAgents(),
          ]);
          await loadAuthContext();
        };
        await loadDashboard();
      } else {
        setMessage({ text: data.message || "Failed to reset database", type: "error" });
      }
    } catch (err) {
      setMessage({ text: "Network error occurred during reset.", type: "error" });
    } finally {
      setIsResetting(false);
    }
  };

  const loadCompanyRegistrations = async () => {
    try {
      const response = await fetch(
        `${URL.COMPANY_REGISTRATIONS}?scope=processed`,
        {
          headers: getAuthHeaders(),
        },
      );

      if (!response.ok)
        throw new Error("Failed to fetch company registrations");

      const requestsList = await response.json();
      requestsList.sort((a, b) => {
        const dateA = a.requested_at ? new Date(a.requested_at) : new Date(0);
        const dateB = b.requested_at ? new Date(b.requested_at) : new Date(0);
        return dateB - dateA;
      });

      setCompanyRegistrations(requestsList);
    } catch (error) {
      console.error("Error loading company registrations:", error);
      setMessage({
        text: "Error loading company registrations",
        type: "error",
      });
    }
  };

  const loadCompanyAgents = async () => {
    try {
      const response = await fetch(URL.COMPANIES, {
        headers: getAuthHeaders(),
      });

      if (!response.ok) {
        throw new Error("Failed to fetch company agents");
      }

      const companies = await response.json();
      setCompanyAgentRows(Array.isArray(companies) ? companies : []);
    } catch (error) {
      console.error("Error loading company agents:", error);
      setMessage({
        text: "Error loading company agents",
        type: "error",
      });
    } finally {
    }
  };

  const loadAdminPayments = async () => {
    setLoading(true);
    try {
      const response = await fetch(URL.ADMIN_PAYMENTS, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch admin payments");
      const data = await response.json();
      setAdminPayments(data);
    } catch (error) {
      console.error("Error loading admin payments:", error);
      showMessage("Error loading payments", "error");
    } finally {
      setLoading(false);
    }
  };

  const loadPendingCompanyRegistrations = async () => {
    try {
      const response = await fetch(
        `${URL.COMPANY_REGISTRATIONS}?scope=pending`,
        {
          headers: getAuthHeaders(),
        },
      );

      if (!response.ok)
        throw new Error("Failed to fetch pending company registrations");

      const requestsList = await response.json();
      const pendingList = Array.isArray(requestsList) ? requestsList : [];
      pendingList.sort((a, b) => {
        const dateA = a.requested_at ? new Date(a.requested_at) : new Date(0);
        const dateB = b.requested_at ? new Date(b.requested_at) : new Date(0);
        return dateB - dateA;
      });
      setPendingCompanyRegistrations(pendingList);
      setPendingCompanyRegistrationsCount(pendingList.length);
    } catch (error) {
      console.error(
        "Error loading pending company registrations count:",
        error,
      );
      setPendingCompanyRegistrations([]);
      setPendingCompanyRegistrationsCount(0);
    }
  };


  const approveCompanyRegistration = async (request) => {
    const confirmed = await showConfirm(
      `Approve ${request.company_name} (${request.domain}) and grant access to the workspace?`,
      {
        title: "Approve company registration?",
        confirmLabel: "Approve company",
        variant: "success",
      }
    );
    if (confirmed) {
      setLoading(true);
      try {
        let requested = [];
        try {
          requested = JSON.parse(request.requested_agents || "[]");
        } catch (e) {
          requested = [];
        }

        const response = await fetch(`${URL.COMPANY_REGISTRATIONS}/${request.id}`, {
          method: "PATCH",
          headers: getAuthHeaders(),
          body: JSON.stringify({
            action: "approve",
            approved_agents: requested,
          }),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(
            errorData.message || "Failed to approve company registration",
          );
        }

        await Promise.all([
          loadCompanyAgents(),
          loadCompanyRegistrations(),
          loadPendingCompanyRegistrations(),
        ]);

        setMessage({
          text: `Registration for ${request.company_name} approved successfully`,
          type: "success",
        });
      } catch (error) {
        console.error("Error approving company registration:", error);
        setMessage({
          text: error.message || "Error approving company registration. Please try again.",
          type: "error",
        });
      } finally {
        setLoading(false);
      }
    }
  };




  const rejectCompanyRegistration = async (request) => {
    const remark = await showPrompt(
      `Explain why ${request.company_name} (${request.domain}) is being rejected.`,
      {
        title: "Reject company registration",
        inputLabel: "Rejection remark",
        placeholder: "Enter a clear reason for the rejection",
        multiline: true,
        rows: 4,
        confirmLabel: "Reject request",
        variant: "danger",
      }
    );
    if (remark === null) {
      return;
    }
    const trimmedRemark = remark.trim();
    if (!trimmedRemark) {
      setMessage({
        text: "Remark is required to reject a company registration.",
        type: "error",
      });
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(
        `${URL.COMPANY_REGISTRATIONS}/${request.id}`,
        {
          method: "PATCH",
          headers: getAuthHeaders(),
          body: JSON.stringify({ action: "reject", remark: trimmedRemark }),
        },
      );

      if (!response.ok)
        throw new Error("Failed to reject company registration");

      await Promise.all([
        loadCompanyRegistrations(),
        loadPendingCompanyRegistrations(),
      ]);
      setMessage({
        text: `Company registration for ${request.company_name} rejected`,
        type: "success",
      });
    } catch (error) {
      console.error("Error rejecting company registration:", error);
      setMessage({
        text: "Error rejecting company registration. Please try again.",
        type: "error",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteCompany = async (companyId, companyName) => {
    if (!companyId) {
      await showAlert("Cannot determine the company ID. Refresh the page and try again.", {
        title: "Company unavailable",
        variant: "danger",
      });
      return;
    }

    const confirmFirst = await showConfirm(
      `Are you sure you want to delete the company "${companyName}"?`,
      {
        title: "Delete company?",
        confirmLabel: "Continue",
        variant: "danger",
      }
    );
    if (!confirmFirst) return;

    const confirmSecond = await showConfirm(
      `Deleting "${companyName}" will permanently remove all of its agents, users, and associated data. This cannot be undone.`,
      {
        title: "Final confirmation",
        confirmLabel: "Permanently delete",
        variant: "danger",
      }
    );
    if (!confirmSecond) return;

    setLoading(true);
    try {
      const response = await fetch(`${URL.COMPANIES}/${companyId}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || data.error || "Failed to delete company");
      }

      showMessage("Company and all associated data deleted successfully.", "success");
      await Promise.all([
        loadCompanyRegistrations(),
        loadCompanyAgents()
      ]);
    } catch (error) {
      console.error("Error deleting company:", error);
      showMessage(error.message || "Failed to delete company", "error");
    } finally {
      setLoading(false);
    }
  };








  const roleOptions = [
    { value: "admin", label: "Administrator" },
    { value: "F", label: "Full Access User" },
  ];


  const validateUserData = (userData) => {
    const errors = [];

    if (!userData.email) {
      errors.push("Email is required");
    } else {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(userData.email)) {
        errors.push("Please enter a valid email address");
      }
    }

    if (!userData.fullName) {
      errors.push("Full name is required");
    } else if (userData.fullName.length < 2) {
      errors.push("Full name must be at least 2 characters long");
    }

    if (!userData.role) {
      errors.push("Role is required");
    }

    return errors;
  };

  const addUser = async () => {
    const errors = validateUserData(newUser);
    if (errors.length > 0) {
      showMessage(errors.join(", "), "error");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(URL.AUTH_USERS, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          email: newUser.email,
          full_name: newUser.fullName,
          role: newUser.role,
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.message || "Failed to add user");
      }

      const addedUser = await response.json();

      setUsers([...users, { ...addedUser, full_name: addedUser.full_name }]); // Maintain full_name consistent with fetch
      setNewUser({ email: "", fullName: "", role: "" });
      setShowAddUserPopup(false); // Close the popup window
      showMessage(`User ${newUser.fullName} added successfully`, "success");
    } catch (error) {
      console.error("Error adding user:", error);
      showMessage(
        error.message || "Error adding user. Please try again.",
        "error",
      );
    } finally {
      setLoading(false);
    }
  };

  const updateUser = async (userEmail, updatedData) => {
    const userToUpdate = users.find((u) => u.email === userEmail);
    if (!userToUpdate) return;

    // Validate (skipping email check as it's not editable)
    if (!updatedData.fullName || updatedData.fullName.length < 2) {
      showMessage("Full name must be at least 2 characters", "error");
      return;
    }
    if (!updatedData.role) {
      showMessage("Role is required", "error");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`${URL.AUTH_USERS}/${userToUpdate.id}`, {
        method: "PATCH",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          full_name: updatedData.fullName,
          role: updatedData.role,
        }),
      });

      if (!response.ok) throw new Error("Failed to update user");

      const updatedUser = await response.json();

      setUsers(
        users.map((user) =>
          user.email === userEmail ? { ...user, ...updatedUser } : user,
        ),
      );
      setEditingUser(null);
      setEditingUserData({});
      showMessage(
        `User ${updatedData.fullName} updated successfully`,
        "success",
      );
    } catch (error) {
      console.error("Error updating user:", error);
      showMessage("Error updating user. Please try again.", "error");
    } finally {
      setLoading(false);
    }
  };

  const deleteUser = async (userEmail) => {
    const user = users.find((u) => u.email === userEmail);
    const userName = user ? user.full_name : userEmail;

    const confirmed = await showConfirm(
      `Delete user "${userName}"? This action cannot be undone.`,
      {
        title: "Delete user?",
        confirmLabel: "Delete user",
        variant: "danger",
      }
    );
    if (!confirmed) return;

    setLoading(true);
    try {
      const response = await fetch(`${URL.AUTH_USERS}/${user.id}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });

      if (!response.ok) throw new Error("Failed to delete user");

      setUsers(users.filter((user) => user.email !== userEmail));
      showMessage(`User ${userName} deleted successfully`, "success");
    } catch (error) {
      console.error("Error deleting user:", error);
      showMessage("Error deleting user. Please try again.", "error");
    } finally {
      setLoading(false);
    }
  };

  const startEditUser = (user) => {
    setEditingUser(user.email);
    setEditingUserData({
      fullName: user.full_name || "",
      role: user.role,
    });
  };

  const handleEditInputChange = (field, value) => {
    setEditingUserData((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const saveUserEdit = (userEmail) => {
    updateUser(userEmail, editingUserData);
  };

  const cancelEdit = () => {
    setEditingUser(null);
    setEditingUserData({});
  };

  const getRoleLabel = (roleValue) => {
    const role = roleOptions.find((r) => r.value === roleValue);
    return role ? role.label : roleValue;
  };

  const backDestination = isSuperAdmin ? "/" : "/dashboard";

  const handleBackToDashboard = () => {
    navigate(backDestination);
  };



  const formatTimestamp = (dateString) => {
    if (!dateString) return "N/A";
    try {
      // Handle SQLite default format (YYYY-MM-DD HH:MM:SS) which is UTC but missing Z
      let accessibleDate = dateString;
      if (
        typeof dateString === "string" &&
        !dateString.includes("T") &&
        dateString.includes(" ")
      ) {
        accessibleDate = dateString.replace(" ", "T") + "Z";
      }

      const date = new Date(accessibleDate);

      // Check if date is valid
      if (isNaN(date.getTime())) {
        // Fallback for other formats
        return new Date(dateString).toLocaleString(undefined, {
          year: "numeric",
          month: "short",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        });
      }

      return date.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "N/A";
    }
  };

  const localDateKey = (dateString) => {
    if (!dateString) return "";
    let accessibleDate = dateString;
    if (
      typeof dateString === "string" &&
      !dateString.includes("T") &&
      dateString.includes(" ")
    ) {
      accessibleDate = dateString.replace(" ", "T") + "Z";
    }
    const date = new Date(accessibleDate);
    if (Number.isNaN(date.getTime())) return "";
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  };

  const databaseAuditVisibleLogs = databaseToolAudit.filter((auditLog) => {
    const userFilterValue = String(databaseAuditAppliedFilters.user || "").trim().toLowerCase();
    if (userFilterValue) {
      const requesterEmail = String(auditLog.requester_email || "").trim().toLowerCase();
      if (requesterEmail !== userFilterValue) return false;
    }
    const dateFilterValue = String(databaseAuditAppliedFilters.date || "").trim();
    if (dateFilterValue && localDateKey(auditLog.created_at) !== dateFilterValue) {
      return false;
    }
    return true;
  });

  const formatUsageNumber = (value) => {
    if (value === null || value === undefined) return "Unlimited";
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    return numeric.toLocaleString();
  };

  const chatLimit = chatUsage?.monthly_token_limit;
  const chatUsed = Number(chatUsage?.tokens_used || 0);
  const chatRemaining = chatUsage?.tokens_remaining;
  const chatUsagePercent = chatLimit ? Math.min(100, Math.round((chatUsed / Number(chatLimit)) * 100)) : 0;
  const pendingDatabaseConfirmations = databaseAuditVisibleLogs.filter(
    (item) => item.status === "pending_user_confirmation"
  ).length;
  const databaseAuditUserOptions = Array.from(
    new Set(users.map((user) => String(user.email || "").trim()).filter(Boolean))
  ).sort((a, b) => a.localeCompare(b));


  return (
    <div className="admin-dashboard-page">
      <Navbar currentPage="admin" />

      <div className="admin-dashboard-container">
        {/* Header */}
        <div className="admin-header">
          <div className="admin-header-left">
            <BackArrow
              onClick={handleBackToDashboard}
              className="admin-back-arrow"
              title="Back to dashboard"
            />
            <div className="AdminHeaderText">
              <h1 className="admin-title">
                <FaUsers className="admin-icon" />
                {isSuperAdmin ? "Super Admin Dashboard" : "Admin Dashboard"}
              </h1>
              <p className="admin-subtitle">
                &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Manage user roles and permissions
              </p>
            </div>
          </div>

          <LogoutButton />
        </div>

        {/* Message Display */}
        {message.text && (
          <div className={`message ${message.type}`}>{message.text}</div>
        )}



        {/* Add New User Button
        <div className="new-user">
          <button className="new-user-button" onClick={toggleAddUserPopup}>
            <FaUserPlus className="new-user-icon" />
            Add New User
          </button>
        </div> */}

        <div className={`admin-action-boxes ${isSuperAdmin ? 'super-admin-mode' : 'regular-admin-mode'}`}>
          {!isSuperAdmin && (
            <>
              <div
                className="user-management-box agents-created-box"
                onClick={() => setShowAgentsCreatedPopup(true)}
              >
                <h3>
                  <FaRobot className="user-icon" />
                  Agents Created ({customAgents.length})
                </h3>
              </div>
              <div
                className="user-management-box company-users-box"
                onClick={() => setShowUserManagementPopup(true)}
              >
                <h3>
                  <FaUsers className="user-icon" />
                  Company Users ({users.length})
                </h3>
              </div>
              <div
                className="user-management-box database-approvals-box"
                onClick={() => {
                  setShowDatabaseAuditPopup(true);
                  loadDatabaseToolAudit();
                }}
              >
                <h3>
                  <FaDatabase className="user-icon" />
                  DB Activity ({pendingDatabaseConfirmations})
                </h3>
              </div>
            </>
          )}

          {/* New Company Registrations Box (Super Admin Only) */}
          {isSuperAdmin && (
            <div
              className="user-management-box"
              onClick={togglePendingCompanyRegistrationsPopup}
            >
              <h3>
                <FaBuilding className="user-icon" />
                Company Registration Requests ({pendingCompanyRegistrationsCount}{" "}
                )
              </h3>
            </div>
          )}

          {/* Payment History Box (Super Admin Only) */}
          {isSuperAdmin && (
            <div
              className="user-management-box"
              onClick={togglePaymentsPopup}
            >
              <h3>
                <FaCreditCard className="user-icon" />
                Payment Details ({adminPayments.length})
              </h3>
            </div>
          )}

          {/* Subscription Packages Box (Super Admin Only) */}
          {isSuperAdmin && (
            <div
              className="user-management-box"
              onClick={togglePackagesPopup}
            >
              <h3>
                <FaLayerGroup className="user-icon" />
                Subscription Packages
              </h3>
            </div>
          )}

          {/* WhatsApp Add-on Box (Super Admin Only) */}
          {isSuperAdmin && (
            <button
              type="button"
              className="user-management-box whatsapp-addon-action"
              onClick={toggleWhatsappAddonPopup}
            >
              <h3>
                <FaWhatsapp className="user-icon" />
                WhatsApp Add-on
              </h3>
            </button>
          )}

          {/* User Management Box removed per request */}




          {/* Legacy Agent Links action removed */}
        </div>


        {/* Workspace Chat Usage */}
        {!isSuperAdmin && (
          <div className="admin-table-section chat-usage-section">
            <div className="section-title-row">
              <div>
                <h2 className="section-title">Workspace Chat Usage</h2>
                <p className="chat-usage-subtitle">
                  Shared monthly quota across all users and custom agents in this company.
                </p>
              </div>
              <button
                type="button"
                className="chat-usage-refresh-btn"
                onClick={loadChatUsage}
                disabled={chatUsageLoading}
              >
                {chatUsageLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>

            {chatUsageLoading && !chatUsage ? (
              <div className="loading-container compact">
                <div className="loading-spinner"></div>
                <p>Loading chat usage...</p>
              </div>
            ) : chatUsage ? (
              <>
                <div className="chat-quota-grid">
                  <div className="chat-quota-card primary">
                    <span className="chat-quota-label">Used this month</span>
                    <strong>{formatUsageNumber(chatUsed)}</strong>
                    <small>of {formatUsageNumber(chatLimit)} tokens</small>
                  </div>
                  <div className="chat-quota-card">
                    <span className="chat-quota-label">Remaining</span>
                    <strong>{formatUsageNumber(chatRemaining)}</strong>
                    <small>resets on {formatTimestamp(chatUsage.period_end)}</small>
                  </div>
                  <div className="chat-quota-card">
                    <span className="chat-quota-label">Active package</span>
                    <strong>{String(chatUsage.tier || "free").toUpperCase()}</strong>
                    <small>{formatTimestamp(chatUsage.period_start)} - {formatTimestamp(chatUsage.period_end)}</small>
                  </div>
                </div>

                <div className="chat-usage-progress" aria-label={`Chat quota used ${chatUsagePercent}%`}>
                  <div style={{ width: `${chatUsagePercent}%` }} />
                </div>

                {(chatUsage.users || []).length > 0 ? (
                  <div className="table-container company-users-table-container chat-activity-table-container">
                    <table className="users-table chat-activity-table">
                      <thead>
                        <tr>
                          <th>User</th>
                          <th>Role</th>
                          <th>Tokens Used</th>
                          <th>Agents Used</th>
                          <th>Quota Share</th>
                          <th>User Limit</th>
                          <th>Last Activity</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(chatUsage.users || []).map((usageUser) => (
                          <tr key={usageUser.user_id || usageUser.email}>
                            <td data-label="User">
                              <div className="chat-activity-user">
                                <strong>{usageUser.full_name || "Unnamed User"}</strong>
                                <span>{usageUser.email}</span>
                              </div>
                            </td>
                            <td data-label="Role">
                              <span className={`role-badge role-${usageUser.role}`}>
                                {getRoleLabel(usageUser.role)}
                              </span>
                            </td>
                            <td data-label="Tokens Used">{formatUsageNumber(usageUser.tokens_used)}</td>
                            <td data-label="Agents Used">{formatUsageNumber(usageUser.agents_used)}</td>
                            <td data-label="Quota Share">{usageUser.quota_share_percent == null ? "N/A" : `${usageUser.quota_share_percent}%`}</td>
                            <td data-label="User Limit">
                              <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                                <span>{usageUser.user_monthly_token_limit == null ? "No cap" : `${formatUsageNumber(usageUser.user_monthly_token_limit)}/mo`}</span>
                                <button
                                  type="button"
                                  className="chat-usage-refresh-btn"
                                  onClick={() => setUserChatQuota(usageUser)}
                                  disabled={savingUserQuotaId === usageUser.user_id}
                                  style={{ padding: "6px 10px" }}
                                >
                                  {savingUserQuotaId === usageUser.user_id ? "Saving..." : "Set"}
                                </button>
                                {usageUser.user_monthly_token_limit != null && (
                                  <button
                                    type="button"
                                    className="chat-usage-refresh-btn"
                                    onClick={() => clearUserChatQuota(usageUser)}
                                    disabled={savingUserQuotaId === usageUser.user_id}
                                    style={{ padding: "6px 10px" }}
                                  >
                                    Clear
                                  </button>
                                )}
                              </div>
                            </td>
                            <td data-label="Last Activity">{formatTimestamp(usageUser.last_activity_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="empty-state">
                    <p>No workspace members have used chat yet.</p>
                  </div>
                )}
              </>
            ) : (
              <div className="empty-state">
                <p>Chat usage is not available right now.</p>
              </div>
            )}
          </div>
        )}

        {showDatabaseAuditPopup && !isSuperAdmin && (
          <div className="popup-overlay-management database-audit-popup-overlay">
            <div className="popup-content-management database-audit-popup-content">
              <button
                className="popup-close-button"
                onClick={() => setShowDatabaseAuditPopup(false)}
              >
                X
              </button>
          <div className="admin-table-section database-audit-section">
            <div className="section-title-row">
              <div>
                <h2 className="section-title">
                  <FaDatabase className="section-icon" />
                  Database Tool Audit
                </h2>
                <p className="chat-usage-subtitle">
                  Review pending database writes and inspect recent database tool activity.
                </p>
              </div>
              <button
                type="button"
                className="chat-usage-refresh-btn"
                onClick={loadDatabaseToolAudit}
                disabled={databaseToolAuditLoading}
              >
                {databaseToolAuditLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>

            <form
              className="database-audit-filters"
              onSubmit={(event) => {
                event.preventDefault();
                loadDatabaseToolAudit({
                  user: databaseAuditUserFilter,
                  date: databaseAuditDateFilter,
                });
              }}
            >
              <label>
                <span>User</span>
                <select
                  value={databaseAuditUserFilter}
                  onChange={(event) => setDatabaseAuditUserFilter(event.target.value)}
                >
                  <option value="">All users</option>
                  {databaseAuditUserOptions.map((email) => (
                    <option key={email} value={email}>{email}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>Date</span>
                <input
                  type="date"
                  value={databaseAuditDateFilter}
                  onChange={(event) => setDatabaseAuditDateFilter(event.target.value)}
                />
              </label>
              <div className="database-audit-filter-actions">
                <button type="submit" disabled={databaseToolAuditLoading}>
                  Apply
                </button>
                <button type="button" onClick={clearDatabaseAuditFilters} disabled={databaseToolAuditLoading}>
                  Clear
                </button>
              </div>
            </form>

            {databaseToolAuditLoading && databaseAuditVisibleLogs.length === 0 ? (
              <div className="loading-container compact">
                <div className="loading-spinner"></div>
                <p>Loading database audit...</p>
              </div>
            ) : databaseAuditVisibleLogs.length > 0 ? (
              <div className="table-container company-users-table-container database-audit-table-container">
                <table className="users-table database-audit-table exclude-responsive">
                  <colgroup>
                    <col />
                    <col />
                    <col />
                    <col />
                    <col />
                    <col />
                    <col />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Agent</th>
                      <th>Operation</th>
                      <th>SQL</th>
                      <th>Impact</th>
                      <th>Requested By</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {databaseAuditVisibleLogs.map((auditLog) => {
                      const resultRowCount = auditLog.result?.response?.row_count;
                      const handledBy = auditLog.approver_display_name || auditLog.rejecter_display_name || auditLog.approver_name || auditLog.rejecter_name;
                      const handledAt = auditLog.approved_at || auditLog.rejected_at || auditLog.executed_at || auditLog.created_at;
                      return (
                        <tr key={auditLog.id}>
                          <td data-label="Status">
                            <span className={`db-audit-status ${auditLog.status || "unknown"}`}>
                              {String(auditLog.status || "unknown").replace(/_/g, " ")}
                            </span>
                          </td>
                          <td data-label="Agent">
                            <div className="chat-activity-user">
                              <strong>{auditLog.agent_name || `Agent ${auditLog.agent_id}`}</strong>
                              <span>{auditLog.profile_name || auditLog.engine || "Database profile"}</span>
                            </div>
                          </td>
                          <td data-label="Operation">
                            <strong className="database-audit-operation">{String(auditLog.operation || "").toUpperCase()}</strong>
                          </td>
                          <td data-label="SQL">
                            <pre className="db-audit-sql">{auditLog.sql_text}</pre>
                            {auditLog.error_message && (
                              <div className="db-audit-error">{auditLog.error_message}</div>
                            )}
                            {auditLog.rejection_reason && (
                              <div className="db-audit-muted">Rejected: {auditLog.rejection_reason}</div>
                            )}
                          </td>
                          <td data-label="Impact">
                            <div className="db-audit-impact">
                              <span>
                                Estimate: {auditLog.estimated_affected_rows == null ? "-" : formatUsageNumber(auditLog.estimated_affected_rows)}
                              </span>
                              <span>
                                Rows: {auditLog.affected_rows == null ? (resultRowCount == null ? "-" : formatUsageNumber(resultRowCount)) : formatUsageNumber(auditLog.affected_rows)}
                              </span>
                              <span>
                                Limit: {formatUsageNumber(auditLog.max_affected_rows)}
                              </span>
                              {auditLog.preview_error && (
                                <small>{auditLog.preview_error}</small>
                              )}
                            </div>
                          </td>
                          <td data-label="Requested By">
                            <span className="db-audit-email">{auditLog.requester_email || "-"}</span>
                          </td>
                          <td data-label="Result">
                            <span className="db-audit-result">
                              {auditLog.status === "pending_user_confirmation"
                                ? "Waiting for user"
                                : handledBy
                                  ? `Handled by ${handledBy} at ${formatTimestamp(handledAt)}`
                                  : auditLog.executed_at
                                    ? `Completed ${formatTimestamp(auditLog.executed_at)}`
                                    : "-"}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty-state">
                <p>No database tool activity matches the current filters.</p>
              </div>
            )}
          </div>
            </div>
          </div>
        )}

        {false && !isSuperAdmin && (
          <div className="admin-table-section">
            <div className="section-title-row">
              <h2 className="section-title">
                Company Users (
                {users.filter((u) =>
                  (u.full_name || "").toLowerCase().includes(userSearch.toLowerCase())
                ).length} of {users.length} users)
              </h2>
              {/* Search bar */}
              <div className="company-search-wrapper">
                <i className="fa fa-search company-search-icon" />
                <input
                  type="text"
                  className="company-search-input"
                  placeholder="Search users by name..."
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                />
                {userSearch && (
                  <button
                    className="company-search-clear"
                    onClick={() => setUserSearch("")}
                    title="Clear search"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
            {loading && (
              <div className="loading-container">
                <div className="loading-spinner"></div>
                <p>Loading users...</p>
              </div>
            )}

            {!loading && users.length === 0 && (
              <div className="empty-state">
                <FaUsers
                  style={{
                    fontSize: "48px",
                    marginBottom: "20px",
                    opacity: 0.5,
                  }}
                />
                <h3>No users found</h3>
                <p>There are no users for your company yet.</p>
              </div>
            )}

            {!loading && users.length > 0 && (() => {
              const filteredUsers = users.filter((u) =>
                (u.full_name || "").toLowerCase().includes(userSearch.toLowerCase())
              );

              return (
                <>
                  {filteredUsers.length === 0 && (
                    <div className="empty-state">
                      <p>No users match &ldquo;<strong>{userSearch}</strong>&rdquo;.</p>
                    </div>
                  )}

                  {filteredUsers.length > 0 && (
                    <div className="table-container company-users-table-container">
                      <table className="users-table">
                        <thead>
                          <tr>
                            <th>Email</th>
                            <th>Full Name</th>
                            <th>Role</th>
                            <th>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredUsers.map((user) => (
                            <tr key={user.email}>
                              <td data-label="Email">{user.email}</td>
                              <td data-label="Full Name">
                                {editingUser === user.email ? (
                                  <input
                                    type="text"
                                    value={editingUserData.fullName}
                                    onChange={(e) =>
                                      handleEditInputChange("fullName", e.target.value)
                                    }
                                    className="inline-input"
                                    placeholder="Full Name"
                                  />
                                ) : (
                                  user.full_name || "N/A"
                                )}
                              </td>
                              <td data-label="Role">
                                {editingUser === user.email ? (
                                  <select
                                    value={editingUserData.role}
                                    onChange={(e) =>
                                      handleEditInputChange("role", e.target.value)
                                    }
                                    className="inline-select"
                                  >
                                    {roleOptions.map((role) => (
                                      <option key={role.value} value={role.value}>
                                        {role.label}
                                      </option>
                                    ))}
                                  </select>
                                ) : (
                                  <span className={`role-badge role-${user.role}`}>
                                    {getRoleLabel(user.role)}
                                  </span>
                                )}
                              </td>
                              <td data-label="Actions">
                                <div className="action-buttons">
                                  {editingUser === user.email ? (
                                    <>
                                      <button
                                        onClick={() => saveUserEdit(user.email)}
                                        className="action-button save-button"
                                        title="Save changes"
                                      >
                                        ✓
                                      </button>
                                      <button
                                        onClick={cancelEdit}
                                        className="action-button cancel-button"
                                        title="Cancel edit"
                                      >
                                        ✕
                                      </button>
                                    </>
                                  ) : (
                                    <>
                                      <button
                                        onClick={() => startEditUser(user)}
                                        className="action-button edit-button"
                                        title="Edit user"
                                      >
                                        <FaEdit />
                                      </button>
                                      <button
                                        onClick={() => deleteUser(user.email)}
                                        className="action-button delete-button"
                                        title="Delete user"
                                      >
                                        <FaTrash />
                                      </button>
                                    </>
                                  )}
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              );
            })()}


          </div>
        )}

        {/* ── Invite Workspace Members ── */}
        {!isSuperAdmin && (
          <div className="admin-table-section">
            <div className="section-title-row">
              <div>
                <h2 className="section-title">Invite Workspace Members</h2>
                <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '13px', margin: '4px 0 0' }}>
                  Enter the teammate's full name and email address. An invitation email with a secure link to set their password will be sent automatically.
                </p>
              </div>
            </div>

            {/* Add Member Form */}
            <div style={{
              display: 'flex',
              gap: '10px',
              marginBottom: '10px',
              flexWrap: 'wrap',
              alignItems: 'center',
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(146,0,220,0.15)',
              borderRadius: '12px',
              padding: '14px 16px',
            }}>
              <input
                type="text"
                placeholder="Full name *"
                value={newMember.full_name}
                onChange={e => setNewMember(m => ({ ...m, full_name: e.target.value }))}
                onKeyDown={e => e.key === 'Enter' && inviteWorkspaceMember()}
                style={{
                  flex: '1',
                  minWidth: '160px',
                  padding: '9px 12px',
                  borderRadius: '8px',
                  border: '1px solid rgba(146,0,220,0.25)',
                  background: 'rgba(0,0,0,0.35)',
                  color: '#fff',
                  fontSize: '13px',
                  outline: 'none',
                }}
              />
              <input
                type="email"
                placeholder="Email address *"
                value={newMember.email}
                onChange={e => setNewMember(m => ({ ...m, email: e.target.value }))}
                onKeyDown={e => e.key === 'Enter' && inviteWorkspaceMember()}
                style={{
                  flex: '1.5',
                  minWidth: '200px',
                  padding: '9px 12px',
                  borderRadius: '8px',
                  border: '1px solid rgba(146,0,220,0.25)',
                  background: 'rgba(0,0,0,0.35)',
                  color: '#fff',
                  fontSize: '13px',
                  outline: 'none',
                }}
              />
              <button
                onClick={inviteWorkspaceMember}
                disabled={addingMember}
                style={{
                  padding: '9px 18px',
                  borderRadius: '8px',
                  background: 'linear-gradient(135deg, #9200dc, #6a00a8)',
                  border: 'none',
                  color: '#fff',
                  fontWeight: '700',
                  fontSize: '13px',
                  cursor: addingMember ? 'not-allowed' : 'pointer',
                  opacity: addingMember ? 0.6 : 1,
                  whiteSpace: 'nowrap',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                  transition: 'all 0.2s',
                }}
              >
                <FaUserPlus size={13} />
                {addingMember ? 'Inviting...' : 'Invite Member'}
              </button>
            </div>

            {pendingMemberInvitations.length > 0 && (
              <div className="invited-members-container">
                <h3 className="invited-members-title">
                  <FaUsers />
                  Pending Member Invitations
                </h3>
                <div className="invited-members-table-container">
                  <table className="users-table invited-members-table">
                    <colgroup>
                      <col />
                      <col />
                      <col />
                    </colgroup>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Invited Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pendingMemberInvitations.map((invite) => (
                        <tr key={invite.id || invite.email}>
                          <td data-label="Name">{invite.full_name}</td>
                          <td data-label="Email">{invite.email}</td>
                          <td data-label="Invited Time">{formatInviteTime(invite.invited_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {isSuperAdmin && (
          <div className="admin-table-section">
            <div className="section-title-row">
              <h2 className="section-title">
                Company Registrations (
                {companyRegistrations.filter(r => {
                  const matchesSearch = (r.company_name || "").toLowerCase().includes(companySearch.toLowerCase());
                  const matchesStatus = statusFilter === "all" || (r.status || "").toLowerCase() === statusFilter.toLowerCase();
                  return matchesSearch && matchesStatus;
                }).length} of {companyRegistrations.length})
              </h2>
              {/* Controls (Search & Filter) */}
              <div className="company-controls-group">
                {/* Search bar */}
                <div className="company-search-wrapper">
                  <i className="fa fa-search company-search-icon" />
                  <input
                    type="text"
                    className="company-search-input"
                    placeholder="Search by company name..."
                    value={companySearch}
                    onChange={(e) => setCompanySearch(e.target.value)}
                  />
                  {companySearch && (
                    <button
                      className="company-search-clear"
                      onClick={() => setCompanySearch("")}
                      title="Clear search"
                    >
                      ✕
                    </button>
                  )}
                </div>

                {/* Filter Dropdown */}
                <select
                  className="company-status-filter"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  <option value="all">All Statuses</option>
                  <option value="approved">Approved</option>
                  <option value="rejected">Rejected</option>
                </select>
              </div>
            </div>
            {loading && (
              <div className="loading-container">
                <div className="loading-spinner"></div>
                <p>Loading processed company registrations...</p>
              </div>
            )}

            {!loading && companyRegistrations.length === 0 && (
              <div className="empty-state">
                <FaBuilding
                  style={{
                    fontSize: "48px",
                    marginBottom: "20px",
                    opacity: 0.5,
                  }}
                />
                <h3>No approved or rejected company registrations</h3>
                <p>Processed company registrations will appear here.</p>
              </div>
            )}

            {!loading && companyRegistrations.length > 0 && (() => {
              const filtered = companyRegistrations.filter(r => {
                const matchesSearch = (r.company_name || "").toLowerCase().includes(companySearch.toLowerCase());
                const matchesStatus = statusFilter === "all" || (r.status || "").toLowerCase() === statusFilter.toLowerCase();
                return matchesSearch && matchesStatus;
              });

              return (
                <>
                  {filtered.length === 0 && (
                    <div className="empty-state">
                      <p>
                        No companies match the criteria{" "}
                        {companySearch && (
                          <>
                            &ldquo;<strong>{companySearch}</strong>&rdquo;
                          </>
                        )}
                        {statusFilter !== "all" && (
                          <>
                            {companySearch && " with "}
                            status <strong>{statusFilter}</strong>
                          </>
                        )}
                        .
                      </p>
                    </div>
                  )}

                  {filtered.length > 0 && (
                    <>
                      {/* Desktop table */}
                      <div className="table-container processed-company-table-container">
                        <table className="users-table">
                          <thead>
                            <tr>
                              <th>Company Name</th>
                              <th>Industry</th>
                              <th>Admin Name</th>
                              <th>Admin Email</th>
                              <th>Agents</th>
                              <th>Requested At</th>
                              <th>Reviewed At</th>
                              <th>Status</th>
                              <th>Remark</th>
                              <th>Actions</th>
                            </tr>
                          </thead>
                          <tbody>
                                {filtered.map((request) => {
                                  const companyData = companyAgentRows.find(
                                    (c) =>
                                      (c.company_name || "").toLowerCase() ===
                                      (request.company_name || "").toLowerCase()
                                  );
                                  return (
                                  <tr key={request.id}>
                                    <td data-label="Company Name">{request.company_name}</td>
                                    <td data-label="Industry">{request.industry || "N/A"}</td>
                                    <td data-label="Admin Name">{request.admin_full_name}</td>
                                    <td data-label="Admin Email">{request.admin_email}</td>
                                    <td data-label="Agents">
                                      {(() => {
                                        const agents = companyData?.agents || [];
                                        return agents.length > 0 ? (
                                          <button
                                            className="view-agents-button"
                                            onClick={() => toggleCompanyAgentsPopup(companyData)}
                                            title="View created agents"
                                          >
                                            View
                                          </button>
                                        ) : (
                                          <span className="table-empty-text">No agents</span>
                                        );
                                      })()}
                                    </td>
                                    <td data-label="Requested At">{formatTimestamp(request.requested_at)}</td>
                                    <td data-label="Reviewed At">{formatTimestamp(request.reviewed_at)}</td>
                                    <td data-label="Status">
                                      <span className={`status-badge status-${(request.status || "").toLowerCase()}`}>
                                        {request.status}
                                      </span>
                                    </td>
                                    <td data-label="Remark">{request.remark || ""}</td>
                                    <td data-label="Actions">
                                      {request.status === "approved" && companyData && (
                                        <button
                                          className="company-delete-button"
                                          onClick={() => handleDeleteCompany(companyData.company_id, companyData.company_name)}
                                          title="Delete company"
                                          style={{
                                            background: '#ef4444',
                                            color: 'white',
                                            border: 'none',
                                            padding: '6px 12px',
                                            borderRadius: '4px',
                                            cursor: 'pointer',
                                            fontSize: '12px',
                                            fontWeight: '600',
                                            transition: 'all 0.2s ease'
                                          }}
                                          onMouseOver={(e) => e.target.style.background = '#dc2626'}
                                          onMouseOut={(e) => e.target.style.background = '#ef4444'}
                                        >
                                          Delete
                                        </button>
                                      )}
                                      {request.status === "approved" && !companyData && (
                                        <span style={{ color: 'rgba(255,255,255,0.4)', fontSize: '12px' }}>N/A</span>
                                      )}
                                    </td>
                                  </tr>
                                  );
                                })}
                          </tbody>
                        </table>
                      </div>

                      {/* Mobile card view — shown only on small screens via CSS */}
                      <div className="company-card-list" style={{ display: 'none' }}>
                        {filtered.map((request) => {
                          const companyData = companyAgentRows.find(
                            (c) =>
                              (c.company_name || "").toLowerCase() ===
                              (request.company_name || "").toLowerCase()
                          );
                          const agents = companyData?.agents || [];
                          return (
                            <div key={request.id} className="company-mobile-card">
                              <div className="cmc-header">
                                <span className="cmc-company-name">{request.company_name}</span>
                                <span className={`status-badge status-${(request.status || "").toLowerCase()}`}>
                                  {request.status}
                                </span>
                              </div>
                              <div className="cmc-row"><span className="cmc-label">Industry</span><span>{request.industry || "N/A"}</span></div>
                              <div className="cmc-row"><span className="cmc-label">Admin</span><span>{request.admin_full_name}</span></div>
                              <div className="cmc-row"><span className="cmc-label">Email</span><span className="cmc-email">{request.admin_email}</span></div>
                              <div className="cmc-row"><span className="cmc-label">Agents</span>
                                <span>
                                  {agents.length > 0 ? (
                                    <button className="view-agents-button" onClick={() => toggleCompanyAgentsPopup(companyData)}>View</button>
                                  ) : "No agents"}
                                </span>
                              </div>
                              <div className="cmc-row"><span className="cmc-label">Requested</span><span>{formatTimestamp(request.requested_at)}</span></div>
                              <div className="cmc-row"><span className="cmc-label">Reviewed</span><span>{formatTimestamp(request.reviewed_at)}</span></div>
                              {request.remark && (
                                <div className="cmc-row"><span className="cmc-label">Remark</span><span>{request.remark}</span></div>
                              )}
                              {request.status === "approved" && companyData && (
                                <div className="cmc-actions">
                                  <button
                                    className="company-delete-button cmc-delete-btn"
                                    onClick={() => handleDeleteCompany(companyData.company_id, companyData.company_name)}
                                  >
                                    Delete Company
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </>
                  )}
                </>
              );
            })()}

          </div>
        )}

        {/* Super Admin Session Details */}
        {isSuperAdmin && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '24px', marginBottom: '24px' }}>
            <button className="super-admin-reset-btn" onClick={handleResetClick}>
              <FaDatabase style={{ marginRight: '6px' }} /> Reset System Database
            </button>
          </div>
        )}

        {/* Add User Popup */}
        {showAddUserPopup && (
          <div className="popup-overlay">
            <div className="popup-content">
              <button
                className="popup-close-button"
                onClick={toggleAddUserPopup}
              >
                ✕
              </button>
              <div className="admin-form-section">
                <h2 className="section-title">
                  <FaUserPlus className="section-icon" />
                  Add New User
                </h2>
                <div className="add-user-form">
                  <div className="form-group">
                    <label>Email Address *</label>
                    <input
                      type="email"
                      value={newUser.email}
                      onChange={(e) =>
                        setNewUser(Object.assign({}, newUser, { email: e.target.value }))
                      }
                      placeholder="user@example.com"
                      className="admin-input"
                      disabled={loading}
                    />
                  </div>
                  <div className="form-group">
                    <label>Full Name *</label>
                    <input
                      type="text"
                      value={newUser.fullName}
                      onChange={(e) =>
                        setNewUser(Object.assign({}, newUser, { fullName: e.target.value }))
                      }
                      placeholder="Sahan Silva"
                      className="admin-input"
                      disabled={loading}
                    />
                  </div>
                  <div className="form-group">
                    <label>Role *</label>
                    <select
                      value={newUser.role}
                      onChange={(e) =>
                        setNewUser(Object.assign({}, newUser, { role: e.target.value }))
                      }
                      className="admin-select"
                      disabled={loading}
                    >
                      <option value="">Select a role</option>
                      {roleOptions.map((role) => (
                        <option key={role.value} value={role.value}>
                          {role.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    onClick={addUser}
                    disabled={loading}
                    className="add-user-button"
                  >
                    <FaUserPlus />
                    {loading ? "Adding..." : "Add User"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}


        {/* Agents Created Popup */}
        {showAgentsCreatedPopup && (
          <div className="popup-overlay-management" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '40px 20px', overflowY: 'auto' }}>
            <div className="popup-content-management" style={{ maxWidth: '900px', width: '100%' }}>
              <button
                className="popup-close-button"
                onClick={() => setShowAgentsCreatedPopup(false)}
                style={{ color: '#ef4444' }}
              >
                ✕
              </button>
              <div className="admin-table-section">
                <h2 className="section-title" style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <FaRobot style={{ color: '#9200dc' }} /> Agents Created ({customAgents.length})
                </h2>
                
                {loading && (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading agent details...</p>
                  </div>
                )}

                {!loading && customAgents.length === 0 && (
                  <div className="empty-state">
                    <FaRobot style={{ fontSize: "48px", marginBottom: "20px", opacity: 0.5 }} />
                    <h3>No agents created yet</h3>
                    <p>Go back to the main workspace dashboard to configure and deploy custom AI agents.</p>
                  </div>
                )}

                {!loading && customAgents.length > 0 && (
                  <div className="table-container">
                    <table className="users-table">
                      <thead>
                        <tr>
                          <th>Agent Name</th>
                          <th>Created At</th>
                          <th>Active Tools</th>
                        </tr>
                      </thead>
                      <tbody>
                        {customAgents.map((agent) => (
                          <tr key={agent.id}>
                            <td data-label="Agent Name" style={{ fontWeight: '600', color: '#fff' }}>{agent.name}</td>
                            <td data-label="Created At">{agent.created_at ? new Date(agent.created_at).toLocaleDateString() : "-"}</td>
                            <td data-label="Active Tools">
                              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                                {agent.tools && agent.tools.map((t, idx) => (
                                  <span key={idx} className="role-badge" style={{ background: 'rgba(146, 0, 220, 0.2)', color: '#d8b4fe', border: '1px solid rgba(146, 0, 220, 0.4)', padding: '2px 8px', borderRadius: '4px', fontSize: '11px' }}>
                                    {t === 'api' ? 'API Tool' : t === 'form_handler' ? 'Form Builder' : t === 'knowledge_base' ? 'Knowledge Base' : t}
                                  </span>
                                ))}
                                {(!agent.tools || agent.tools.length === 0) && "-"}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {companyInfo && (
                  <div style={{
                    marginTop: '30px',
                    padding: '24px',
                    background: 'rgba(255, 255, 255, 0.02)',
                    border: '1px solid rgba(255, 255, 255, 0.08)',
                    borderRadius: '16px',
                    textAlign: 'left'
                  }}>
                    <h4 style={{ color: '#fff', fontSize: '15px', fontWeight: '700', marginBottom: '12px' }}>
                      Subscription Management
                    </h4>
                    <p style={{ color: '#aaa', fontSize: '13px', margin: '0 0 20px', lineHeight: '1.5' }}>
                      Active Plan: <strong style={{ color: '#d8b4fe' }}>{companyInfo.subscription_tier ? companyInfo.subscription_tier.toUpperCase() : "FREE"}</strong>
                      {companyInfo.subscription_expires_at && (
                        <> | Expires: <strong style={{ color: '#fff' }}>{new Date(companyInfo.subscription_expires_at).toLocaleDateString()}</strong></>
                      )}
                    </p>
                    
                    <div style={{ display: 'flex', gap: '15px' }}>
                      {companyInfo.subscription_tier !== 'free' && (
                        <button
                          onClick={handleExtendSubscription}
                          disabled={loading}
                          style={{
                            background: '#9200dc',
                            color: '#fff',
                            padding: '10px 24px',
                            borderRadius: '8px',
                            border: 'none',
                            fontWeight: '600',
                            cursor: 'pointer',
                            boxShadow: '0 4px 12px rgba(146, 0, 220, 0.3)',
                            transition: 'all 0.3s'
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = '#8000c4'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = '#9200dc'; }}
                        >
                          Extend Subscription
                        </button>
                      )}
                      {companyInfo.subscription_tier !== 'free' && (
                        <button
                          onClick={handleCancelSubscription}
                          disabled={loading}
                          style={{
                            background: 'rgba(239, 68, 68, 0.1)',
                            color: '#ef4444',
                            border: '1px solid rgba(239, 68, 68, 0.3)',
                            padding: '10px 24px',
                            borderRadius: '8px',
                            fontWeight: '600',
                            cursor: 'pointer',
                            transition: 'all 0.3s'
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)';
                            e.currentTarget.style.borderColor = '#ef4444';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)';
                            e.currentTarget.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                          }}
                        >
                          Cancel Subscription
                        </button>
                      )}
                      {companyInfo.subscription_tier === 'free' && (
                        <span style={{ color: '#777', fontSize: '12px', fontStyle: 'italic' }}>
                          Go to your workspace dashboard to upgrade to a premium subscription plan.
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Pending Company Registrations Popup (Super Admin Only) */}
        {isSuperAdmin && showPendingCompanyRegistrationsPopup && (
          <div className="popup-overlay-management">
            <div className="popup-content-management">
              <button
                className="popup-close-button"
                onClick={togglePendingCompanyRegistrationsPopup}
              >
                X
              </button>
              <div className="admin-table-section">
                <h2 className="section-title">
                  Pending Company Registrations (
                  {pendingCompanyRegistrations.length} pending)
                </h2>
                {loading && (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading company registrations...</p>
                  </div>
                )}

                {!loading && pendingCompanyRegistrations.length === 0 && (
                  <div className="empty-state">
                    <FaBuilding
                      style={{
                        fontSize: "48px",
                        marginBottom: "20px",
                        opacity: 0.5,
                      }}
                    />
                    <h3>No pending company registrations</h3>
                    <p>
                      All company registration requests have been processed.
                    </p>
                  </div>
                )}

                {!loading && pendingCompanyRegistrations.length > 0 && (
                  <div className="table-container">
                    <table className="users-table">
                      <thead>
                        <tr>
                          <th style={{ width: "13%" }}>Company Name</th>
                          <th style={{ width: "15%" }}>Domain</th>
                          <th style={{ width: "9%" }}>Industry</th>
                          <th style={{ width: "13%" }}>Admin Name</th>
                          <th style={{ width: "18%" }}>Admin Email</th>
                          <th style={{ width: "16%" }}>Requested At</th>
                          <th style={{ width: "200px" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pendingCompanyRegistrations.map((request) => (
                          <tr key={request.id}>
                            <td data-label="Company Name">{request.company_name}</td>
                            <td data-label="Domain">{request.domain}</td>

                            <td data-label="Industry">{request.industry || "N/A"}</td>
                            <td data-label="Admin Name">{request.admin_full_name}</td>
                            <td data-label="Admin Email">{request.admin_email}</td>
                            <td data-label="Requested At">{formatTimestamp(request.requested_at)}</td>
                            <td data-label="Actions">
                              <div className="action-buttons">
                                <button
                                  onClick={() =>
                                    approveCompanyRegistration(request)
                                  }
                                  className="action-button approve-request-btn"
                                  title="Approve request"
                                  disabled={loading}
                                >
                                  Approve
                                </button>
                                <button
                                  onClick={() =>
                                    rejectCompanyRegistration(request)
                                  }
                                  className="action-button reject-request-btn"
                                  title="Reject request"
                                  disabled={loading}
                                >
                                  Reject
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Payments Details Popup (Super Admin Only) */}
        {isSuperAdmin && showPaymentsPopup && (
          <div className="popup-overlay-management">
            <div className="popup-content-management">
              <button
                className="popup-close-button"
                onClick={togglePaymentsPopup}
              >
                ✕
              </button>
              <div className="admin-table-section">
                <h2 className="section-title">
                  <FaCreditCard className="section-icon" />
                  Payment Details ({adminPayments.length} transactions)
                </h2>
                {loading && (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading payments...</p>
                  </div>
                )}

                {!loading && adminPayments.length === 0 && (
                  <div className="empty-state">
                    <FaCreditCard
                      style={{
                        fontSize: "48px",
                        marginBottom: "20px",
                        opacity: 0.5,
                      }}
                    />
                    <h3>No payments found</h3>
                    <p>There are no recorded transactions.</p>
                  </div>
                )}

                {!loading && adminPayments.length > 0 && (
                  <div className="table-container">
                    <table className="users-table">
                      <thead>
                        <tr>
                          <th>Company Name</th>
                          <th>Purchase</th>
                          <th>Transaction ID</th>
                          <th>Gateway</th>
                          <th>Amount</th>
                          <th>Status</th>
                          <th>Payment Date</th>
                          <th>Expire Date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {adminPayments.map((payment) => {
                          const txId = payment.stripe_session_id || payment.transaction_id || payment.session_id || "";
                          const displayTxId = (payment.transaction_id && payment.transaction_id.startsWith("T_ID "))
                            ? payment.transaction_id
                            : (payment.id ? `T_ID ${String(payment.id).padStart(2, '0')}` : (txId.length > 15 ? txId.slice(0, 12) + "..." : txId || "N/A"));

                          let expiresAt = payment.expires_at;
                          if (!expiresAt) {
                            const pDate = new Date(payment.payment_date || payment.created_at || new Date());
                            if (payment.amount < 1.0) {
                              pDate.setDate(pDate.getDate() + 7);
                            } else {
                              pDate.setDate(pDate.getDate() + 30);
                            }
                            expiresAt = pDate.toISOString();
                          }

                          let displayPlanName = payment.plan_name;
                          if (!displayPlanName) {
                            if (payment.amount < 1.0) {
                              displayPlanName = "Free Trial";
                            } else if (payment.amount >= 149.0) {
                              displayPlanName = "Enterprise Plan";
                            } else if (payment.amount >= 49.0) {
                              displayPlanName = "Business Plan";
                            } else {
                              displayPlanName = "Starter Plan";
                            }
                          }

                          return (
                            <tr key={payment.id || payment.session_id}>
                              <td data-label="Company Name">{payment.company_name || "N/A"}</td>
                              <td data-label="Purchase" style={{ fontWeight: '600' }}>{displayPlanName}</td>
                              <td data-label="Transaction ID" title={txId} style={{ fontFamily: 'monospace', cursor: 'help' }}>{displayTxId}</td>
                              <td data-label="Gateway">{payment.payment_gateway || "N/A"}</td>
                              <td data-label="Amount">
                                {payment.amount !== undefined ? (
                                  `$${payment.amount.toFixed(2)} ${String(payment.currency || "USD").toUpperCase()}`
                                ) : "N/A"}
                              </td>
                              <td data-label="Status">
                                <span className={`status-badge status-${(payment.status || "").toLowerCase()}`}>
                                  {payment.status}
                                </span>
                              </td>
                              <td data-label="Payment Date">{formatTimestamp(payment.payment_date || payment.created_at)}</td>
                              <td data-label="Expire Date">{formatTimestamp(expiresAt)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Subscription Packages Popup (Super Admin Only) */}
        {isSuperAdmin && showPackagesPopup && (
          <div className="popup-overlay-management">
            <div className="popup-content-management package-config-popup">
              <button
                className="popup-close-button"
                onClick={togglePackagesPopup}
              >
                ✕
              </button>
              <div className="admin-table-section">
                <h2 className="section-title">
                  <FaLayerGroup className="section-icon" />
                  Subscription Package Configuration
                </h2>
                <p className="package-config-subtitle">
                  Edit Starter, Business, and Enterprise limits in one place. Changes apply to pricing, billing, and enforcement across the platform.
                </p>
                {packagesUpdatedAt && (
                  <p className="package-config-updated">
                    Last updated: {formatTimestamp(packagesUpdatedAt)}
                  </p>
                )}

                {loading ? (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading subscription packages...</p>
                  </div>
                ) : (
                  <>
                    <div className="package-config-list">
                      {PACKAGE_ORDER.map((packageId) => (
                        <PackageConfigCard
                          key={packageId}
                          packageId={packageId}
                          config={packageConfigDraft[packageId] || {}}
                          onChange={updatePackageConfigField}
                        />
                      ))}
                    </div>
                     <div className="package-config-actions">
                      <button
                        type="button"
                        className="package-save-config-btn"
                        onClick={saveSubscriptionPackages}
                        disabled={savingPackages}
                      >
                        {savingPackages ? "Saving..." : "Save Package Configuration"}
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {/* WhatsApp Add-on Popup (Super Admin Only) */}
        {isSuperAdmin && showWhatsappAddonPopup && (
          <div className="popup-overlay-management">
            <div className="popup-content-management whatsapp-addon-config-popup">
              <button
                type="button"
                className="popup-close-button"
                onClick={toggleWhatsappAddonPopup}
                aria-label="Close WhatsApp add-on configuration"
              >
                &times;
              </button>
              <div className="admin-table-section">
                <div className="whatsapp-addon-config-heading">
                  <span className="whatsapp-addon-config-icon" aria-hidden="true">
                    <FaWhatsapp />
                  </span>
                  <div>
                    <h2 className="section-title">WhatsApp Add-on</h2>
                    <p>Global monthly billing configuration</p>
                  </div>
                  <span className={`whatsapp-addon-config-status ${whatsappAddonDraft.available ? "is-available" : ""}`}>
                    {whatsappAddonDraft.available ? "Available" : "Unavailable"}
                  </span>
                </div>

                {whatsappAddonLoading ? (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading WhatsApp add-on...</p>
                  </div>
                ) : (
                  <form className="whatsapp-addon-config-form" onSubmit={saveWhatsappAddonConfig}>
                    <div className="whatsapp-addon-config-grid">
                      <label>
                        Monthly Price (USD)
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          inputMode="decimal"
                          value={whatsappAddonDraft.monthly_price_usd}
                          onChange={(event) => setWhatsappAddonDraft((current) => ({
                            ...current,
                            monthly_price_usd: event.target.value,
                          }))}
                          disabled={savingWhatsappAddon}
                        />
                      </label>
                    </div>

                    <label className="whatsapp-addon-availability">
                      <span>
                        <strong>Available for purchase</strong>
                        <small>Controls new company purchases</small>
                      </span>
                      <input
                        type="checkbox"
                        checked={whatsappAddonDraft.available}
                        onChange={(event) => setWhatsappAddonDraft((current) => ({
                          ...current,
                          available: event.target.checked,
                        }))}
                        disabled={savingWhatsappAddon}
                      />
                      <span className="whatsapp-addon-switch" aria-hidden="true"></span>
                    </label>

                    <p className="whatsapp-addon-effective-note">
                      Price changes take effect on renewal. WhatsApp agent conversations use the workspace token quota.
                    </p>
                    {whatsappAddonUpdatedAt && (
                      <p className="whatsapp-addon-updated-at">
                        Last updated: {formatTimestamp(whatsappAddonUpdatedAt)}
                      </p>
                    )}
                    {whatsappAddonError && (
                      <div className="whatsapp-addon-config-error" role="alert">
                        {whatsappAddonError}
                      </div>
                    )}

                    <div className="package-config-actions">
                      <button
                        type="submit"
                        className="package-save-config-btn whatsapp-addon-save-btn"
                        disabled={savingWhatsappAddon}
                      >
                        <FaSave aria-hidden="true" />
                        {savingWhatsappAddon ? "Saving..." : "Save Configuration"}
                      </button>
                    </div>
                  </form>
                )}
              </div>
            </div>
          </div>
        )}

        {/* User Management Popup */}
        {showUserManagementPopup && (
          <div className="popup-overlay-management">
            <div className="popup-content-management">
              <button
                className="popup-close-button"
                onClick={toggleUserManagementPopup}
              >
                ✕
              </button>
              <div className="admin-table-section">
                <h2 className="section-title">
                  {isSuperAdmin ? "User Management" : "Company Users"} ({users.length} users)
                </h2>
                {loading && (
                  <div className="loading-container">
                    <div className="loading-spinner"></div>
                    <p>Loading users...</p>
                  </div>
                )}

                {!loading && users.length === 0 && (
                  <div className="empty-state">
                    <FaUsers
                      style={{
                        fontSize: "48px",
                        marginBottom: "20px",
                        opacity: 0.5,
                      }}
                    />
                    <h3>No users found</h3>
                    <p>{isSuperAdmin ? "Add your first user using the form above." : "There are no users for your company yet."}</p>
                  </div>
                )}

                {!loading && users.length > 0 && (
                  <div className="table-container">
                    <table className="users-table">
                      <thead>
                        <tr>
                          <th style={{ width: "35%" }}>Email</th>
                          <th style={{ width: "30%" }}>Full Name</th>
                          <th style={{ width: "20%" }}>Role</th>
                          <th style={{ width: "15%" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.map((user) => (
                          <tr key={user.email}>
                            <td data-label="Email">{user.email}</td>
                            <td data-label="Full Name">
                              {editingUser === user.email ? (
                                <input
                                  type="text"
                                  value={editingUserData.fullName}
                                  onChange={(e) =>
                                    handleEditInputChange(
                                      "fullName",
                                      e.target.value,
                                    )
                                  }
                                  className="inline-input"
                                  placeholder="Full Name"
                                />
                              ) : (
                                user.full_name || "N/A"
                              )}
                            </td>
                            <td data-label="Role">
                              {editingUser === user.email ? (
                                <select
                                  value={editingUserData.role}
                                  onChange={(e) =>
                                    handleEditInputChange(
                                      "role",
                                      e.target.value,
                                    )
                                  }
                                  className="inline-select"
                                >
                                  {roleOptions.map((role) => (
                                    <option key={role.value} value={role.value}>
                                      {role.label}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <span
                                  className={`role-badge role-${user.role}`}
                                >
                                  {getRoleLabel(user.role)}
                                </span>
                              )}
                            </td>
                            <td data-label="Actions">
                              <div className="action-buttons">
                                {editingUser === user.email ? (
                                  <>
                                    <button
                                      onClick={() => saveUserEdit(user.email)}
                                      className="action-button save-button"
                                      title="Save changes"
                                    >
                                      ✓
                                    </button>
                                    <button
                                      onClick={cancelEdit}
                                      className="action-button cancel-button"
                                      title="Cancel edit"
                                    >
                                      ✕
                                    </button>
                                  </>
                                ) : (
                                  <>
                                    <button
                                      onClick={() => startEditUser(user)}
                                      className="action-button edit-button"
                                      title="Edit user"
                                    >
                                      <FaEdit />
                                    </button>
                                    <button
                                      onClick={() => deleteUser(user.email)}
                                      className="action-button delete-button"
                                      title="Delete user"
                                    >
                                      <FaTrash />
                                    </button>
                                  </>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Role descriptions removed as requested */}
      </div>



      {/* Agent Resources Popup */}
      {showAgentResourcesPopup && (
        <div className="popup-overlay-management">
          <div
            className="popup-content-management"
            style={{ maxWidth: "720px" }}
          >
            <button
              className="popup-close-button"
              onClick={toggleAgentResourcesPopup}
            >
              ✕
            </button>
            <div className="admin-table-section">
              <h2 className="section-title">
                <i className="fas fa-link" style={{ marginRight: "10px" }}></i>
                Agent Knowledge Links
              </h2>
              <p className="resource-popup-subtitle">
                Configure knowledge sources and authentication for your agents.
              </p>

              {companyAllowedAgents.length > 0 && (
                <div className="agent-selector-container">
                  <label>Select Agent to Manage:</label>
                  <select
                    className="agent-select-dropdown"
                    value={selectedAgentForResources}
                    onChange={(e) => setSelectedAgentForResources(e.target.value)}
                  >
                    {companyAllowedAgents.map(code => (
                      <option key={code} value={code}>
                        {code} (ID: {code})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {loading && !agentResources.length ? (
                <div className="loading-container">
                  <div className="loading-spinner"></div>
                  <p>Loading agent configuration...</p>
                </div>
              ) : (
                <div className="resource-cards-container">
                  {selectedAgentForResources ? (
                    (() => {
                      const code = selectedAgentForResources;
                      const resource =
                        agentResources.find((r) => r.agent_code === code) ||
                        {};
                      return (
                        <AgentResourceCard
                          key={code}
                          code={code}
                          label={code}
                          initialOneDrive={resource.onedrive_link || ""}
                          initialWebsite={resource.website_link || ""}
                          initialAccessToken={resource.access_token || ""}
                          onSave={handleSaveAgentResource}
                          onIngest={handleIngest}
                          disabled={loading}
                          ingestionTask={ingestionTask}
                        />
                      );
                    })()
                  ) : (
                    <div className="empty-state">
                      <p>No agents have been granted to your company yet.</p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* View Company Agents Popup */}
      {showCompanyAgentsPopup && selectedCompanyForAgents && (
        <div className="popup-overlay-management popup-overlay-center">
          <div className="popup-content-management" style={{ maxWidth: "600px" }}>
            <button
              className="popup-close-button"
              onClick={() => toggleCompanyAgentsPopup()}
            >
              ✕
            </button>
            <div className="popup-table-section">
              <h2 className="section-title">
                <FaUsers className="section-icon" />
                Agents for {selectedCompanyForAgents.company_name}
              </h2>
              <div className="agents-list-modal">
                {selectedCompanyForAgents.agents && selectedCompanyForAgents.agents.length > 0 ? (
                  <div className="modal-agents-grid">
                    {selectedCompanyForAgents.agents.map((agent) => (
                      <div key={agent.id} className="modal-agent-item">
                        <span className="agent-pill-large">{agent.name}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="empty-state">No custom agents created yet.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      {/* Reset Database Step 1 Confirmation Modal */}
      {showResetStep1 && (
        <div className="popup-overlay-management popup-overlay-center reset-overlay">
          <div className="popup-content-management reset-content" style={{ maxWidth: "450px" }}>
            <h2 className="section-title reset-title">
              <FaDatabase className="section-icon reset-icon" />
              Confirm Database Reset
            </h2>
            <div className="reset-body">
              <p>You are about to initiate a system-wide database reset.</p>
              <p>This will **permanently delete** all client companies, users, subscriptions, custom agents, tools, and chat messages. Only the Super Admin accounts and workspaces will be preserved.</p>
              <p className="reset-warning">Warning: This action is destructive and cannot be undone.</p>
            </div>
            <div className="reset-actions">
              <button className="reset-action-btn cancel-btn" onClick={handleResetCancel}>
                Cancel
              </button>
              <button className="reset-action-btn confirm-btn" onClick={handleResetStep1Confirm}>
                Proceed
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset Database Step 2 Final Verification Modal */}
      {showResetStep2 && (
        <div className="popup-overlay-management popup-overlay-center reset-overlay">
          <div className="popup-content-management reset-content" style={{ maxWidth: "450px" }}>
            <h2 className="section-title reset-title danger-title">
              <FaDatabase className="section-icon reset-icon" />
              Final Security Check
            </h2>
            <div className="reset-body">
              <p>To confirm that you want to wipe all records from the database, please type <strong>RESET</strong> in capital letters in the input field below:</p>
              <input
                type="text"
                className="reset-verification-input"
                placeholder="Type RESET here"
                value={resetConfirmationText}
                onChange={(e) => setResetConfirmationText(e.target.value)}
                disabled={isResetting}
              />
            </div>
            <div className="reset-actions">
              <button className="reset-action-btn cancel-btn" onClick={handleResetCancel} disabled={isResetting}>
                Cancel
              </button>
              <button
                className={`reset-action-btn submit-reset-btn ${resetConfirmationText === "RESET" ? "unlocked" : "locked"}`}
                onClick={handleResetFinalConfirm}
                disabled={resetConfirmationText !== "RESET" || isResetting}
              >
                {isResetting ? "Resetting System..." : "Confirm & Reset Database"}
              </button>
            </div>
          </div>
        </div>
      )}

      <Footer />
    </div>
  );
};

export default AdminDashboard;


