import React, { useEffect, useState } from "react";
import {
  FaBuilding,
  FaCalendarAlt,
  FaCheckCircle,
  FaCopy,
  FaCreditCard,
  FaLock,
  FaPaperPlane,
  FaPhoneAlt,
  FaSave,
  FaTimes,
  FaTimesCircle,
  FaTrash,
  FaUndoAlt,
  FaWhatsapp,
} from "react-icons/fa";
import { URL } from "../urls";
import { useAppDialog } from "./AppDialog";

const ConfigurationStatus = ({ icon, label, value, tone = "pending" }) => (
  <div className="whatsapp-config-status-row">
    <span className={`whatsapp-config-status-icon whatsapp-config-status-icon--${tone}`}>
      {icon}
    </span>
    <span className="whatsapp-config-status-label">{label}</span>
    <span className={`whatsapp-config-status-value whatsapp-config-status-value--${tone}`}>
      {value}
    </span>
  </div>
);

const EMPTY_CONNECTION = {
  configured: false,
  provider: "meta",
  phone_number: null,
  meta_phone_number_id: null,
  access_token_configured: false,
  waha_session: null,
  waha_api_key_configured: false,
  outbound_ready: false,
};
const EMPTY_WEBHOOK = {
  callback_url: "",
  verify_token: "",
  hmac_secret: "",
  app_secret_configured: false,
  verified_at: null,
};
const EMPTY_ADDON = {
  offer: {
    display_name: "WhatsApp Add-on",
    monthly_price_cents: 0,
    monthly_price_usd: 0,
    currency: "usd",
    available: false,
  },
  subscription: {
    status: "not_purchased",
  },
  can_purchase: false,
};
const DEFAULT_AUTH_HEADERS = () => ({ "Content-Type": "application/json" });
const NOOP = () => {};
const DEFAULT_CHECKOUT_REDIRECT = (url) => window.location.assign(url);
const formatBillingDate = (value) => {
  if (!value) return "Billing period end unavailable";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Billing period end unavailable";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
};

const WhatsAppConfiguration = ({
  open,
  onClose,
  entitlement,
  getAuthHeaders = DEFAULT_AUTH_HEADERS,
  onConnectionChange = NOOP,
  onCheckoutRedirect = DEFAULT_CHECKOUT_REDIRECT,
}) => {
  const { showConfirm } = useAppDialog();
  const [workspaceEntitlement, setWorkspaceEntitlement] = useState(
    entitlement || { enabled: false }
  );
  const isEnabled = workspaceEntitlement?.enabled === true;
  const [connection, setConnection] = useState(EMPTY_CONNECTION);
  const [webhook, setWebhook] = useState(EMPTY_WEBHOOK);
  const [addon, setAddon] = useState(EMPTY_ADDON);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [provider, setProvider] = useState("meta");
  const [metaAppSecret, setMetaAppSecret] = useState("");
  const [metaPhoneNumberId, setMetaPhoneNumberId] = useState("");
  const [metaAccessToken, setMetaAccessToken] = useState("");
  const [wahaSession, setWahaSession] = useState("");
  const [wahaApiKey, setWahaApiKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [subscriptionLoading, setSubscriptionLoading] = useState(false);
  const [feedback, setFeedback] = useState({ type: "", text: "" });
  const [subscriptionFeedback, setSubscriptionFeedback] = useState({ type: "", text: "" });

  useEffect(() => {
    setWorkspaceEntitlement(entitlement || { enabled: false });
  }, [entitlement]);

  useEffect(() => {
    if (!open) return undefined;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return undefined;

    let active = true;
    const loadConfiguration = async () => {
      setLoading(true);
      setFeedback({ type: "", text: "" });
      setSubscriptionFeedback({ type: "", text: "" });
      try {
        const response = await fetch(URL.WHATSAPP_CONFIGURATION, {
          headers: getAuthHeaders(),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.message || "Failed to load WhatsApp configuration.");
        }
        if (!active) return;
        const nextConnection = data.connection || EMPTY_CONNECTION;
        const nextWebhook = data.webhook || EMPTY_WEBHOOK;
        setWorkspaceEntitlement(data.entitlement || entitlement || { enabled: false });
        setConnection(nextConnection);
        setWebhook(nextWebhook);
        setAddon(data.addon || EMPTY_ADDON);
        setPhoneNumber(nextConnection.phone_number || "");
        setProvider(nextConnection.provider || "meta");
        setMetaPhoneNumberId(nextConnection.meta_phone_number_id || "");
        setWahaSession(nextConnection.waha_session || "");
        onConnectionChange(nextConnection);
      } catch (error) {
        if (active) {
          setFeedback({
            type: "error",
            text: error.message || "Failed to load WhatsApp configuration.",
          });
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    loadConfiguration();
    return () => {
      active = false;
    };
  }, [open, entitlement, getAuthHeaders, onConnectionChange]);

  const startCheckout = async () => {
    setCheckoutLoading(true);
    setFeedback({ type: "", text: "" });
    try {
      const response = await fetch(URL.WHATSAPP_ADDON_CHECKOUT, {
        method: "POST",
        headers: getAuthHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || "Failed to start WhatsApp checkout.");
      }
      if (!data.url) {
        throw new Error("Stripe Checkout did not return a redirect URL.");
      }
      setAddon((current) => ({
        ...current,
        subscription: {
          ...current.subscription,
          ...(data.subscription || {}),
          status: data.subscription?.status || "checkout_pending",
        },
      }));
      onCheckoutRedirect(data.url);
    } catch (error) {
      setFeedback({
        type: "error",
        text: error.message || "Failed to start WhatsApp checkout.",
      });
    } finally {
      setCheckoutLoading(false);
    }
  };

  const manageSubscription = async (action) => {
    if (action === "cancel") {
      const confirmed = await showConfirm(
        "WhatsApp access will remain active until the current billing period ends, but it will not renew.",
        {
          title: "Cancel WhatsApp renewal?",
          confirmLabel: "Cancel renewal",
          variant: "danger",
        }
      );
      if (!confirmed) return;
    }

    setSubscriptionLoading(true);
    setSubscriptionFeedback({ type: "", text: "" });
    try {
      const response = await fetch(URL.WHATSAPP_ADDON_SUBSCRIPTION, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ action }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || "Failed to update WhatsApp renewal.");
      }
      setAddon(data.addon || EMPTY_ADDON);
      setWorkspaceEntitlement(data.entitlement || { enabled: true });
      setSubscriptionFeedback({
        type: "success",
        text: data.message || "WhatsApp renewal updated.",
      });
    } catch (error) {
      setSubscriptionFeedback({
        type: "error",
        text: error.message || "Failed to update WhatsApp renewal.",
      });
    } finally {
      setSubscriptionLoading(false);
    }
  };

  const savePhoneNumber = async (event) => {
    event.preventDefault();
    setSaving(true);
    setFeedback({ type: "", text: "" });
    try {
      const response = await fetch(URL.WHATSAPP_CONFIGURATION, {
        method: "PUT",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          phone_number: phoneNumber,
          provider,
          ...(provider === "meta"
            ? {
                ...(metaAppSecret.trim() ? { meta_app_secret: metaAppSecret.trim() } : {}),
                ...(metaPhoneNumberId.trim()
                  ? { meta_phone_number_id: metaPhoneNumberId.trim() }
                  : {}),
                ...(metaAccessToken.trim()
                  ? { meta_access_token: metaAccessToken.trim() }
                  : {}),
              }
            : {
                ...(wahaSession.trim() ? { waha_session: wahaSession.trim() } : {}),
                ...(wahaApiKey.trim() ? { waha_api_key: wahaApiKey.trim() } : {}),
              }),
        }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || "Failed to save the WhatsApp number.");
      }
      const nextConnection = data.connection || EMPTY_CONNECTION;
      const nextWebhook = data.webhook || EMPTY_WEBHOOK;
      setConnection(nextConnection);
      setWebhook(nextWebhook);
      setPhoneNumber(nextConnection.phone_number || "");
      setProvider(nextConnection.provider || provider);
      setMetaPhoneNumberId(nextConnection.meta_phone_number_id || "");
      setWahaSession(nextConnection.waha_session || "");
      setMetaAppSecret("");
      setMetaAccessToken("");
      setWahaApiKey("");
      onConnectionChange(nextConnection);
      setFeedback({ type: "success", text: "WhatsApp number saved." });
    } catch (error) {
      setFeedback({
        type: "error",
        text: error.message || "Failed to save the WhatsApp number.",
      });
    } finally {
      setSaving(false);
    }
  };

  const removePhoneNumber = async () => {
    const confirmed = await showConfirm(
      "The connected WhatsApp number will be removed from this workspace.",
      {
        title: "Remove WhatsApp number?",
        confirmLabel: "Remove number",
        variant: "danger",
      }
    );
    if (!confirmed) return;

    setSaving(true);
    setFeedback({ type: "", text: "" });
    try {
      const response = await fetch(URL.WHATSAPP_CONFIGURATION, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || "Failed to remove the WhatsApp number.");
      }
      const nextConnection = data.connection || EMPTY_CONNECTION;
      setConnection(nextConnection);
      setWebhook(EMPTY_WEBHOOK);
      setPhoneNumber("");
      setProvider("meta");
      setMetaPhoneNumberId("");
      setMetaAppSecret("");
      setMetaAccessToken("");
      setWahaSession("");
      setWahaApiKey("");
      onConnectionChange(nextConnection);
      setFeedback({ type: "success", text: "WhatsApp number removed." });
    } catch (error) {
      setFeedback({
        type: "error",
        text: error.message || "Failed to remove the WhatsApp number.",
      });
    } finally {
      setSaving(false);
    }
  };

  const copyValue = async (value, label) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setFeedback({ type: "success", text: `${label} copied.` });
    } catch {
      setFeedback({ type: "error", text: `Could not copy ${label.toLowerCase()}.` });
    }
  };

  if (!open) return null;

  return (
    <div
      className="whatsapp-config-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="whatsapp-config-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="whatsapp-config-title"
      >
        <header className="whatsapp-config-header">
          <div className="whatsapp-config-heading">
            <span className="whatsapp-config-brand-icon" aria-hidden="true">
              <FaWhatsapp />
            </span>
            <div>
              <h2 id="whatsapp-config-title">WhatsApp Configuration</h2>
              <p>{isEnabled ? "Workspace channel setup" : "Workspace add-on purchase"}</p>
            </div>
          </div>
          <button
            type="button"
            className="whatsapp-config-close"
            onClick={onClose}
            aria-label="Close WhatsApp configuration"
            title="Close"
          >
            <FaTimes />
          </button>
        </header>

        <div className="whatsapp-config-body">
          {isEnabled ? (
            <>
              <div className="whatsapp-config-summary whatsapp-config-summary--active">
                <FaCheckCircle aria-hidden="true" />
                <div>
                  <strong>
                    {addon.subscription?.cancel_at_period_end
                      ? "WhatsApp cancellation scheduled"
                      : "WhatsApp add-on active"}
                  </strong>
                  <span>
                    {addon.subscription?.cancel_at_period_end
                      ? `Access remains active through ${formatBillingDate(addon.subscription?.current_period_end)}.`
                      : "This workspace can proceed with channel configuration."}
                  </span>
                </div>
              </div>

              {(addon.can_cancel || addon.can_reactivate) && (
                <div className="whatsapp-addon-management">
                  <div className="whatsapp-addon-management-details">
                    <span className="whatsapp-addon-management-icon" aria-hidden="true">
                      <FaCalendarAlt />
                    </span>
                    <div>
                      <strong>Monthly subscription</strong>
                      <span>
                        {addon.subscription?.cancel_at_period_end
                          ? `Ends ${formatBillingDate(addon.subscription?.current_period_end)}`
                          : `Renews ${formatBillingDate(addon.subscription?.current_period_end)}`}
                      </span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className={addon.can_reactivate
                      ? "whatsapp-config-primary"
                      : "whatsapp-config-remove"}
                    onClick={() => manageSubscription(
                      addon.can_reactivate ? "reactivate" : "cancel"
                    )}
                    disabled={subscriptionLoading}
                  >
                    {addon.can_reactivate ? (
                      <FaUndoAlt aria-hidden="true" />
                    ) : (
                      <FaTimesCircle aria-hidden="true" />
                    )}
                    {subscriptionLoading
                      ? "Updating"
                      : (addon.can_reactivate ? "Restore renewal" : "Cancel renewal")}
                  </button>
                  <span
                    className={`whatsapp-config-feedback whatsapp-config-feedback--${subscriptionFeedback.type}`}
                    role={subscriptionFeedback.type === "error" ? "alert" : "status"}
                  >
                    {subscriptionFeedback.text}
                  </span>
                </div>
              )}

              <div className="whatsapp-config-status-list" aria-label="WhatsApp configuration status">
                <ConfigurationStatus
                  icon={<FaCheckCircle />}
                  label="Workspace access"
                  value="Active"
                  tone="active"
                />
                <ConfigurationStatus
                  icon={<FaBuilding />}
                  label={provider === "waha" ? "WAHA session" : "Meta business account"}
                  value={
                    webhook.verified_at
                      ? (provider === "waha" ? "Webhook active" : "Webhook verified")
                      : (provider === "waha"
                        ? (webhook.hmac_secret ? "Awaiting first event" : "Setup required")
                        : (webhook.app_secret_configured ? "Awaiting verification" : "App secret required"))
                  }
                  tone={webhook.verified_at ? "active" : "pending"}
                />
                <ConfigurationStatus
                  icon={<FaPhoneAlt />}
                  label="WhatsApp number"
                  value={loading ? "Loading" : (connection.phone_number || "Not configured")}
                  tone={connection.configured ? "active" : "pending"}
                />
                <ConfigurationStatus
                  icon={<FaPaperPlane />}
                  label="Outbound messaging"
                  value={connection.outbound_ready ? "Configured" : "Credentials required"}
                  tone={connection.outbound_ready ? "active" : "pending"}
                />
              </div>

              <form className="whatsapp-number-form" onSubmit={savePhoneNumber}>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-provider">Messaging provider</label>
                  <select
                    id="whatsapp-provider"
                    value={provider}
                    onChange={(event) => setProvider(event.target.value)}
                    disabled={loading || saving}
                  >
                    <option value="meta">Meta Cloud API</option>
                    <option value="waha">WAHA</option>
                  </select>
                </div>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-phone-number">WhatsApp business number</label>
                  <input
                    id="whatsapp-phone-number"
                    type="tel"
                    value={phoneNumber}
                    onChange={(event) => setPhoneNumber(event.target.value)}
                    placeholder="+94771234567"
                    autoComplete="tel"
                    disabled={loading || saving}
                    required
                  />
                </div>
                {provider === "meta" ? <>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-meta-app-secret">Meta app secret</label>
                  <input
                    id="whatsapp-meta-app-secret"
                    type="password"
                    value={metaAppSecret}
                    onChange={(event) => setMetaAppSecret(event.target.value)}
                    placeholder={
                      webhook.app_secret_configured
                        ? "Leave blank to keep current secret"
                        : "Enter Meta app secret"
                    }
                    autoComplete="new-password"
                    disabled={loading || saving}
                  />
                </div>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-meta-phone-number-id">Meta Phone Number ID</label>
                  <input
                    id="whatsapp-meta-phone-number-id"
                    type="text"
                    inputMode="numeric"
                    value={metaPhoneNumberId}
                    onChange={(event) => setMetaPhoneNumberId(event.target.value)}
                    placeholder="Enter Phone Number ID"
                    autoComplete="off"
                    disabled={loading || saving}
                  />
                </div>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-meta-access-token">Meta system-user access token</label>
                  <input
                    id="whatsapp-meta-access-token"
                    type="password"
                    value={metaAccessToken}
                    onChange={(event) => setMetaAccessToken(event.target.value)}
                    placeholder={
                      connection.access_token_configured
                        ? "Leave blank to keep current token"
                        : "Enter system-user access token"
                    }
                    autoComplete="new-password"
                    disabled={loading || saving}
                  />
                </div>
                </> : <>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-waha-session">WAHA session</label>
                  <input
                    id="whatsapp-waha-session"
                    type="text"
                    value={wahaSession}
                    onChange={(event) => setWahaSession(event.target.value)}
                    placeholder="default"
                    autoComplete="off"
                    disabled={loading || saving}
                    required
                  />
                </div>
                <div className="whatsapp-config-field">
                  <label htmlFor="whatsapp-waha-api-key">WAHA API key</label>
                  <input
                    id="whatsapp-waha-api-key"
                    type="password"
                    value={wahaApiKey}
                    onChange={(event) => setWahaApiKey(event.target.value)}
                    placeholder={
                      connection.waha_api_key_configured
                        ? "Leave blank to keep current key"
                        : "Enter WAHA API key"
                    }
                    autoComplete="new-password"
                    disabled={loading || saving}
                  />
                </div>
                </>}
                <div className="whatsapp-config-save-row">
                  <button
                    type="submit"
                    className="whatsapp-config-primary"
                    disabled={loading || saving}
                  >
                    <FaSave aria-hidden="true" />
                    {saving ? "Saving" : "Save setup"}
                  </button>
                </div>
                <div className="whatsapp-number-form-footer">
                  <span
                    className={`whatsapp-config-feedback whatsapp-config-feedback--${feedback.type}`}
                    role={feedback.type === "error" ? "alert" : "status"}
                  >
                    {feedback.text}
                  </span>
                  {connection.configured && (
                    <button
                      type="button"
                      className="whatsapp-config-remove"
                      onClick={removePhoneNumber}
                      disabled={saving}
                    >
                      <FaTrash aria-hidden="true" />
                      Remove number
                    </button>
                  )}
                </div>
              </form>

              {webhook.callback_url && (
                <div className="whatsapp-webhook-settings">
                  <div className="whatsapp-webhook-settings-header">
                    <strong>{provider === "waha" ? "WAHA webhook" : "Meta webhook"}</strong>
                    <span className={webhook.verified_at ? "is-verified" : ""}>
                      {webhook.verified_at ? "Active" : "Pending"}
                    </span>
                  </div>
                  <label htmlFor="whatsapp-callback-url">Callback URL</label>
                  <div className="whatsapp-copy-field">
                    <input id="whatsapp-callback-url" readOnly value={webhook.callback_url} />
                    <button
                      type="button"
                      onClick={() => copyValue(webhook.callback_url, "Callback URL")}
                      aria-label="Copy callback URL"
                      title="Copy callback URL"
                    >
                      <FaCopy />
                    </button>
                  </div>
                  <label htmlFor="whatsapp-verify-token">
                    {provider === "waha" ? "HMAC secret" : "Verify token"}
                  </label>
                  <div className="whatsapp-copy-field">
                    <input
                      id="whatsapp-verify-token"
                      readOnly
                      value={provider === "waha" ? webhook.hmac_secret : webhook.verify_token}
                    />
                    <button
                      type="button"
                      onClick={() => copyValue(
                        provider === "waha" ? webhook.hmac_secret : webhook.verify_token,
                        provider === "waha" ? "HMAC secret" : "Verify token"
                      )}
                      aria-label={provider === "waha" ? "Copy HMAC secret" : "Copy verify token"}
                      title={provider === "waha" ? "Copy HMAC secret" : "Copy verify token"}
                    >
                      <FaCopy />
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="whatsapp-config-summary whatsapp-config-summary--locked">
                <FaLock aria-hidden="true" />
                <div>
                  <strong>
                    {addon.subscription?.status === "checkout_pending"
                      ? "WhatsApp checkout pending"
                      : "WhatsApp add-on required"}
                  </strong>
                  <span>
                    {addon.subscription?.status === "checkout_pending"
                      ? "Complete checkout to begin subscription activation."
                      : "Purchase the monthly add-on to configure this workspace channel."}
                  </span>
                </div>
              </div>

              <div className="whatsapp-addon-purchase">
                <div className="whatsapp-addon-offer-row">
                  <div>
                    <span>Monthly price</span>
                    <strong>
                      {new Intl.NumberFormat("en-US", {
                        style: "currency",
                        currency: String(addon.offer?.currency || "usd").toUpperCase(),
                      }).format(Number(addon.offer?.monthly_price_usd || 0))}
                    </strong>
                  </div>
                  <div>
                    <span>Usage allowance</span>
                    <strong>Shared workspace token quota</strong>
                  </div>
                </div>

                {addon.offer?.available ? (
                  addon.can_purchase ? (
                    <button
                      type="button"
                      className="whatsapp-config-primary whatsapp-addon-checkout"
                      onClick={startCheckout}
                      disabled={loading || checkoutLoading}
                    >
                      <FaCreditCard aria-hidden="true" />
                      {checkoutLoading
                        ? "Opening checkout"
                        : (addon.subscription?.status === "checkout_pending"
                          ? "Continue checkout"
                          : "Purchase add-on")}
                    </button>
                  ) : (
                    <p className="whatsapp-addon-purchase-state">
                      Subscription status: {String(addon.subscription?.status || "pending").replaceAll("_", " ")}
                    </p>
                  )
                ) : (
                  <p className="whatsapp-addon-purchase-state">
                    Purchasing is currently unavailable.
                  </p>
                )}

                <span
                  className={`whatsapp-config-feedback whatsapp-config-feedback--${feedback.type}`}
                  role={feedback.type === "error" ? "alert" : "status"}
                >
                  {feedback.text}
                </span>
              </div>
            </>
          )}
        </div>

        <footer className="whatsapp-config-footer">
          <button type="button" className="whatsapp-config-secondary" onClick={onClose}>
            Close
          </button>
        </footer>
      </section>
    </div>
  );
};

export default WhatsAppConfiguration;
