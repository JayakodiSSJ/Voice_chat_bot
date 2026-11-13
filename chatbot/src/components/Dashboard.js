import React, { useState, useEffect, useCallback, useRef } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import "../css.css";
import BackArrow from "./BackArrow";
import LogoutButton from "./LogoutButton";
import Footer from './Footer';
import Navbar from "./Navbar";
import AgentWhatsAppAccessOption from "./AgentWhatsAppAccessOption";
import WhatsAppConfiguration from "./WhatsAppConfiguration";
import { FaUserCog, FaRobot, FaPlus, FaPlug, FaDatabase, FaTrash, FaEdit, FaSyncAlt, FaWhatsapp } from 'react-icons/fa';
import { getToken } from "../auth/authClient";
import { URL } from "../urls";
import {
  buildPackageFeatures,
  buildPackageLimitations,
  formatAgentLimitDisplay,
  getTierAgentLimit,
  tierAllowsEndpointTools,
  tierAllowsFormBuilder,
  tierAllowsOneDrive,
  tierAllowsSharePoint,
} from "../utils/subscriptionPackages";
import { useMsal } from "@azure/msal-react";
import { loginRequest, tokenRequest } from "../authConfig";
import { useAppDialog } from "./AppDialog";

const parseAgentConfiguration = (configuration) => {
  if (!configuration) return {};
  if (typeof configuration === "string") {
    try {
      return JSON.parse(configuration);
    } catch {
      return {};
    }
  }
  return typeof configuration === "object" ? configuration : {};
};

const hasKnowledgeBaseConfig = (configuration = {}) => {
  const kb = configuration.knowledge_base || configuration.knowledgeBase || configuration.knowledgebase || {};
  if (Array.isArray(kb)) return kb.length > 0;
  if (typeof kb === "string") return Boolean(kb.trim());
  if (!kb || typeof kb !== "object") return false;

  const values = [
    kb.web_url,
    kb.webUrl,
    kb.web_urls,
    kb.webUrls,
    kb.web_sources,
    kb.webSources,
    kb.onedrive_folder_id,
    kb.onedriveFolderId,
    kb.onedrive_folder_ids,
    kb.onedriveFolderIds,
    kb.sharepoint_folder_id,
    kb.sharepointFolderId,
    kb.sharepoint_folder_ids,
    kb.sharepointFolderIds,
    kb.folder_id,
    kb.folderId,
    kb.folder_ids,
    kb.folderIds,
    kb.sharepoint_folder_id,
    kb.sharepointFolderId,
    kb.sharepoint_folder_ids,
    kb.sharepointFolderIds,
  ];
  return values.some((value) => {
    if (Array.isArray(value)) return value.length > 0;
    return Boolean(String(value || "").trim());
  });
};

const DEFAULT_WEB_CRAWL_SETTINGS = {
  maxPages: 25,
  maxDepth: 1,
  restrictToPath: true,
};

const WEB_CRAWL_HELP = {
  maxPages: "The most pages this website source will read.",
  maxDepth: "How many link levels to follow from the starting URL.",
  restrictToPath: "Stay inside the same website section as the starting URL.",
};

const API_PARAMETER_LOCATION_HELP = {
  path: {
    label: "Path Param",
    description: "Use when the value is part of the endpoint URL. Put a matching placeholder in the URL, such as /employees/{employee_id}/leave_balances, then add key employee_id here."
  },
  query: {
    label: "Query Param",
    description: "Use for values appended to the URL after ?. For example key year with value 2026 becomes ?year=2026."
  },
  body: {
    label: "Body (JSON)",
    description: "Use for values sent as a JSON request body, usually with POST, PUT, PATCH, or DELETE."
  },
  header: {
    label: "Header",
    description: "Use for request metadata such as X-API-KEY, Authorization, Content-Type, tenant IDs, or other custom headers."
  }
};

const getSelectedApiParameterHelp = (parameters = []) => {
  const selectedLocations = parameters
    .map(param => String(param?.location || "query").trim().toLowerCase())
    .filter(location => API_PARAMETER_LOCATION_HELP[location]);
  return Array.from(new Set(selectedLocations)).map(location => API_PARAMETER_LOCATION_HELP[location]);
};

const clampNumber = (value, fallback, min, max) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
};

const normalizeWebCrawlSettings = (settings = {}) => ({
  maxPages: clampNumber(settings.maxPages ?? settings.max_pages, DEFAULT_WEB_CRAWL_SETTINGS.maxPages, 1, 100),
  maxDepth: clampNumber(settings.maxDepth ?? settings.max_depth, DEFAULT_WEB_CRAWL_SETTINGS.maxDepth, 0, 5),
  restrictToPath: settings.restrictToPath ?? settings.restrict_to_start_path ?? DEFAULT_WEB_CRAWL_SETTINGS.restrictToPath,
});

const webCrawlPayload = (kb = {}) => {
  const settings = normalizeWebCrawlSettings(kb.crawl || kb.crawlSettings || {});
  return {
    max_pages: settings.maxPages,
    max_depth: settings.maxDepth,
    restrict_to_start_path: Boolean(settings.restrictToPath),
  };
};

const normalizeToolName = (tool) => String(tool || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

const isMcpToolConfig = (tool) => {
  if (typeof tool === "string") return normalizeToolName(tool) === "mcp";
  if (!tool || typeof tool !== "object") return false;
  const type = normalizeToolName(tool.type || tool.id || tool.name);
  const kind = normalizeToolName(tool.kind || tool.config?.kind);
  return type === "mcp" || kind === "mcp";
};

const isRestApiToolConfig = (tool) => {
  if (isMcpToolConfig(tool)) return false;
  if (isDatabaseToolConfig(tool)) return false;
  if (typeof tool === "string") return normalizeToolName(tool) === "api";
  if (!tool || typeof tool !== "object") return false;
  return normalizeToolName(tool.type || tool.id || tool.name) === "api";
};

const isDatabaseToolConfig = (tool) => {
  if (typeof tool === "string") return ["database", "db", "structured_database"].includes(normalizeToolName(tool));
  if (!tool || typeof tool !== "object") return false;
  const type = normalizeToolName(tool.type || tool.id || tool.name);
  const kind = normalizeToolName(tool.kind || tool.config?.kind);
  return ["database", "db", "structured_database"].includes(type) || ["database", "db", "structured_database"].includes(kind);
};

const getConfiguredToolLabels = (agent) => {
  const configuration = parseAgentConfiguration(agent?.configuration);
  const tools = Array.isArray(configuration.tools) ? configuration.tools : [];
  const normalizedTools = new Set(
    tools
      .map((tool) => (typeof tool === "string" ? tool : tool?.type || tool?.id || tool?.name))
      .map((tool) => String(tool || "").trim().toLowerCase())
      .filter(Boolean)
  );

  const hasApi = tools.some(isRestApiToolConfig) || Boolean(configuration.api || configuration.api_config || configuration.apiConfig);
  const hasMcp = tools.some(isMcpToolConfig) || Boolean(configuration.mcp || configuration.mcp_config || configuration.mcpConfig);
  const hasDatabase = (
    tools.some(isDatabaseToolConfig) ||
    Boolean(configuration.database || configuration.database_config || configuration.databaseConfig || configuration.structured_database || configuration.structuredDatabase)
  );
  const hasForm = (
    normalizedTools.has("form") ||
    normalizedTools.has("forms") ||
    normalizedTools.has("form_handler") ||
    Boolean(configuration.form_handlers || configuration.formHandlers || configuration.forms || configuration.form)
  );
  const hasKb = (
    normalizedTools.has("knowledge_base") ||
    normalizedTools.has("knowledgebase") ||
    hasKnowledgeBaseConfig(configuration)
  );

  return [
    hasApi ? "API Tool" : null,
    hasMcp ? "MCP Tool" : null,
    hasDatabase ? "Database Tool" : null,
    hasForm ? "Form Tool" : null,
    hasKb ? "Knowledge Base" : null,
  ].filter(Boolean);
};

const isAgentExpired = (agent) => {
  if (!agent) return false;
  if (agent.expire_date) {
    return new Date() > new Date(agent.expire_date);
  }
  if (!agent.created_at) return false;
  const createdAt = new Date(agent.created_at);
  const expiryDate = new Date(createdAt.getTime() + 30 * 24 * 60 * 60 * 1000);
  return new Date() > expiryDate;
};

const getFormSubmitActions = (config = {}) => {
  const rawActions = config.submitActions || config.submissionActions;
  if (Array.isArray(rawActions) && rawActions.length) {
    return rawActions.map(action => String(action || "").trim().toLowerCase()).filter(Boolean);
  }
  const legacyAction = String(config.submitAction || config.submissionAction || "email").trim().toLowerCase();
  if (legacyAction === "both") return ["email", "api"];
  return legacyAction === "api" ? ["api"] : ["email"];
};

const resetApiVerification = {
  verified: false,
  verifiedSignature: "",
  verificationMessage: "",
  verificationResult: null
};

const createEmptyRoutingProfile = () => ({
  purpose: '',
  use_when: [],
  do_not_use_when: [],
  data_scope: '',
  capabilities: [],
  requirements: [],
  example_requests: [],
  counterexamples: [],
});

const routingList = (value) => {
  if (Array.isArray(value)) return value.map(item => String(item || '').trim()).filter(Boolean);
  return String(value || '').split(/\r?\n|,/).map(item => item.trim()).filter(Boolean);
};

const normalizeRoutingProfile = (value = {}) => {
  const profile = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  return {
    purpose: profile.purpose || profile.description || '',
    use_when: routingList(profile.use_when || profile.useWhen),
    do_not_use_when: routingList(profile.do_not_use_when || profile.doNotUseWhen || profile.avoid_when || profile.avoidWhen),
    data_scope: profile.data_scope || profile.dataScope || '',
    capabilities: routingList(profile.capabilities),
    requirements: routingList(profile.requirements),
    example_requests: routingList(profile.example_requests || profile.exampleRequests || profile.examples),
    counterexamples: routingList(profile.counterexamples || profile.counter_examples || profile.counterExamples),
  };
};

const routingProfilePayload = (value = {}) => {
  const profile = normalizeRoutingProfile(value);
  return Object.fromEntries(
    Object.entries(profile).filter(([, fieldValue]) => (
      Array.isArray(fieldValue) ? fieldValue.length > 0 : Boolean(String(fieldValue || '').trim())
    ))
  );
};

const preserveMcpToolRoutingProfiles = (existingTools, detectedTools) => {
  const profilesByName = new Map(
    (Array.isArray(existingTools) ? existingTools : [])
      .filter(tool => tool?.name)
      .map(tool => [String(tool.name), tool.routing_profile || tool.routingProfile || {}])
  );
  return (Array.isArray(detectedTools) ? detectedTools : []).map(tool => ({
    ...tool,
    ...(profilesByName.has(String(tool.name))
      ? { routing_profile: routingProfilePayload(profilesByName.get(String(tool.name))) }
      : {}),
  }));
};

const RoutingProfileEditor = ({ value, onChange, title = 'Tool routing guidance' }) => {
  const sourceProfile = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const profile = normalizeRoutingProfile(value);
  const editorState = { ...profile, ...sourceProfile };
  const updateText = (field, fieldValue) => onChange({ ...editorState, [field]: fieldValue });
  const updateList = (field, fieldValue) => onChange({ ...editorState, [field]: fieldValue });
  const listValue = (field) => (
    typeof sourceProfile[field] === 'string' ? sourceProfile[field] : profile[field].join('\n')
  );
  const inputStyle = { width: '100%', background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '9px', borderRadius: '7px', resize: 'vertical', fontSize: '12px' };
  const labelStyle = { color: 'rgba(255,255,255,0.72)', fontSize: '11px', fontWeight: 700, display: 'block', marginBottom: '5px' };

  return (
    <details style={{ marginBottom: '20px', padding: '12px', border: '1px solid rgba(176,38,255,0.25)', borderRadius: '9px', background: 'rgba(146,0,220,0.06)' }}>
      <summary style={{ color: '#d8b4fe', cursor: 'pointer', fontSize: '12px', fontWeight: 800 }}>{title} (optional)</summary>
      <p style={{ color: 'rgba(255,255,255,0.58)', fontSize: '12px', lineHeight: 1.45, margin: '10px 0 14px' }}>
        Add organization-specific guidance that helps the agent choose between overlapping capabilities. Enter one item per line in list fields.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '12px' }}>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Purpose</label>
          <textarea rows={2} maxLength={800} placeholder="What user goal does this capability help accomplish?" style={inputStyle} value={profile.purpose} onChange={(event) => updateText('purpose', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Use when</label>
          <textarea rows={3} maxLength={2400} placeholder={'The user needs current account data\nThe user asks to create a record'} style={inputStyle} value={listValue('use_when')} onChange={(event) => updateList('use_when', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Do not use when</label>
          <textarea rows={3} maxLength={2400} placeholder={'The request only needs general guidance\nA different system owns the record'} style={inputStyle} value={listValue('do_not_use_when')} onChange={(event) => updateList('do_not_use_when', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Data scope</label>
          <textarea rows={2} maxLength={800} placeholder="Systems, teams, regions, tenants, or data covered" style={inputStyle} value={profile.data_scope} onChange={(event) => updateText('data_scope', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Capabilities</label>
          <textarea rows={2} maxLength={1200} placeholder={'Search records\nCreate requests\nUpdate status'} style={inputStyle} value={listValue('capabilities')} onChange={(event) => updateList('capabilities', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Example user requests</label>
          <textarea rows={3} maxLength={2400} placeholder={'Find order 1042\nCancel my pending order'} style={inputStyle} value={listValue('example_requests')} onChange={(event) => updateList('example_requests', event.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Requests that should go elsewhere</label>
          <textarea rows={3} maxLength={2400} placeholder={'Explain the returns policy\nCompare products'} style={inputStyle} value={listValue('counterexamples')} onChange={(event) => updateList('counterexamples', event.target.value)} />
        </div>
        <div style={{ gridColumn: '1 / -1' }}>
          <label style={labelStyle}>Requirements or prerequisites</label>
          <textarea rows={2} maxLength={1600} placeholder={'Requires a customer ID\nUser must be authenticated'} style={inputStyle} value={listValue('requirements')} onChange={(event) => updateList('requirements', event.target.value)} />
        </div>
      </div>
    </details>
  );
};

const DATABASE_ENGINE_OPTIONS = [
  { value: "postgres", label: "PostgreSQL", defaultPort: "5432" },
  { value: "mysql", label: "MySQL / MariaDB", defaultPort: "3306" },
  { value: "sqlserver", label: "SQL Server / Azure SQL", defaultPort: "1433" },
  { value: "sqlite", label: "SQLite", defaultPort: "" },
  { value: "oracle", label: "Oracle", defaultPort: "1521" },
  { value: "odbc", label: "Generic ODBC", defaultPort: "" },
];

const DATABASE_PERMISSION_OPTIONS = [
  { value: "read_only", label: "Read", description: "SELECT" },
  { value: "append_only", label: "Add", description: "SELECT, INSERT" },
  { value: "read_update", label: "Edit", description: "SELECT, UPDATE" },
  { value: "read_insert_update", label: "Add & Edit", description: "SELECT, INSERT, UPDATE" },
  { value: "read_write", label: "Full Write", description: "SELECT, INSERT, UPDATE, DELETE" },
];

const MAX_DATABASE_PROFILES_PER_AGENT = 3;

const databaseModeAllowsColumnWrites = (mode) =>
  ["append_only", "read_update", "read_insert_update", "read_write"].includes(String(mode || "read_only"));

const DATABASE_FIELD_LABEL_STYLE = { color: '#fff', display: 'block', marginBottom: '7px', fontSize: '12px', fontWeight: 700 };
const DATABASE_INPUT_STYLE = { width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' };
const DATABASE_LIMIT_LABEL_STYLE = { color: '#fff', opacity: 0.68, fontSize: '11px', display: 'block', marginBottom: '6px', fontWeight: 700 };
const DATABASE_SECTION_TITLE_STYLE = { color: 'rgba(255,255,255,0.72)', fontSize: '11px', fontWeight: 800, letterSpacing: '1px', textTransform: 'uppercase', margin: '0 0 10px' };

const getDatabaseEngineOption = (engine) =>
  DATABASE_ENGINE_OPTIONS.find(option => option.value === String(engine || "").toLowerCase()) ||
  DATABASE_ENGINE_OPTIONS[0];

const createDefaultDatabaseToolConfig = (overrides = {}) => ({
  kind: 'database',
  engine: 'postgres',
  name: '',
  purpose: '',
  host: '',
  port: '5432',
  database: '',
  username: '',
  password: '',
  sslmode: 'prefer',
  driver: '',
  dsn: '',
  filePath: '',
  serviceName: '',
  connectionUri: '',
  useConnectionUri: false,
  permissionMode: 'read_only',
  selectedSchemas: [],
  selectedTables: [],
  selectedColumns: {},
  detectedSchemas: [],
  limits: { maxRows: 100, timeoutSeconds: 15, maxResultBytes: 12000, maxAffectedRows: 10 },
  ...resetApiVerification,
  ...overrides
});

const apiToolSignature = (config = {}) => JSON.stringify({
  kind: String(config.kind || config.type || "rest").trim().toLowerCase(),
  url: String(config.url || "").trim(),
  method: String(config.method || "GET").trim().toUpperCase(),
  format: String(config.format || "JSON").trim(),
  parameters: (config.parameters || []).map(param => ({
    key: String(param.key || "").trim(),
    location: String(param.location || "query").trim().toLowerCase(),
    type: String(param.type || "string").trim(),
    value: String(param.value || "")
  })),
  testPrompt: String(config.testPrompt || "").trim()
});

const mcpToolSignature = (config = {}) => JSON.stringify({
  url: String(config.url || "").trim(),
  name: String(config.name || "").trim(),
  authType: String(config.authType || "none").trim().toLowerCase(),
  authToken: String(config.authToken || ""),
  authHeaderName: String(config.authHeaderName || "").trim()
});

const databaseToolSignature = (config = {}) => JSON.stringify({
  engine: String(config.engine || "postgres").trim().toLowerCase(),
  name: String(config.name || "").trim(),
  host: String(config.host || "").trim(),
  port: String(config.port || "5432").trim(),
  database: String(config.database || "").trim(),
  username: String(config.username || "").trim(),
  password: String(config.password || ""),
  sslmode: String(config.sslmode || "prefer").trim(),
  driver: String(config.driver || "").trim(),
  dsn: String(config.dsn || "").trim(),
  filePath: String(config.filePath || "").trim(),
  serviceName: String(config.serviceName || "").trim(),
  permissionMode: String(config.permissionMode || "read_only").trim(),
  selectedColumns: config.selectedColumns || {},
  connectionUri: String(config.connectionUri || "").trim()
});

const normalizeMcpAuthConfig = (config = {}) => {
  const auth = config.auth && typeof config.auth === "object" ? config.auth : {};
  return {
    authType: String(config.authType || config.auth_type || auth.type || "none").trim().toLowerCase(),
    authToken: String(config.authToken || config.auth_token || auth.token || auth.value || ""),
    authHeaderName: String(
      config.authHeaderName ||
      config.auth_header_name ||
      auth.headerName ||
      auth.header_name ||
      auth.name ||
      ""
    ).trim()
  };
};

const formatApiVerificationPreview = (value) => {
  if (value === null || value === undefined || value === "") return "";
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return text.length > 1200 ? `${text.slice(0, 1200)}...` : text;
};

const Dashboard = ({ userRole, user, company }) => {
  const isSuperAdmin = user?.is_super_admin || false;
  const navigate = useNavigate();
  const { showAlert, showConfirm } = useAppDialog();
  const [userInfo, setUserInfo] = useState({
    firstName: '',
    lastName: '',
    email: ''
  });

  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState({ text: "", type: "" });
  const [customAgents, setCustomAgents] = useState([]);
  const [showKnowledgebasePopup, setShowKnowledgebasePopup] = useState(false);
  const [agentModeChoice, setAgentModeChoice] = useState(null);
  const [showWhatsappConfiguration, setShowWhatsappConfiguration] = useState(false);
  const [whatsappConnection, setWhatsappConnection] = useState({
    configured: false,
    phone_number: null,
  });
  const [validationErrors, setValidationErrors] = useState({});
  const [hasKbSelected, setHasKbSelected] = useState(false);
  const [hasApiSelected, setHasApiSelected] = useState(false);
  const [hasMcpSelected, setHasMcpSelected] = useState(false);
  const [hasDatabaseSelected, setHasDatabaseSelected] = useState(false);
  const [hasFormSelected, setHasFormSelected] = useState(false);
  const [agentForm, setAgentForm] = useState({
    name: "",
    description: "",
    tools: [],
    knowledgeBase: [{ type: 'onedrive', value: '' }],
    kbRoutingProfile: createEmptyRoutingProfile(),
    microsoft_token: "",
    whatsappEnabled: false,
  });
  const [syncingKbIndex, setSyncingKbIndex] = useState(null);
  const [_payments, setPayments] = useState([]); // eslint-disable-line no-unused-vars
  const [showPaymentSuccessModal, setShowPaymentSuccessModal] = useState(false);
  const [successPaymentDetails, setSuccessPaymentDetails] = useState(null);
  const verificationStarted = useRef(false);

  const [showGraphToken, setShowGraphToken] = useState(false);
  const [showBillingPopup, setShowBillingPopup] = useState(false);
  const shouldShowOnboarding =
    userRole === 'admin' &&
    !isSuperAdmin &&
    company &&
    Number(company.onboarding_completed || 0) === 0 &&
    !company.onboarding_seen_at;
  const [showOnboarding, setShowOnboarding] = useState(Boolean(shouldShowOnboarding));
  const [subscriptionPackages, setSubscriptionPackages] = useState([]);
  const { instance, accounts } = useMsal();
  const activeTier = (company?.subscription_tier || "free").toLowerCase();
  const freePkg = subscriptionPackages.find(p => String(p.id).toLowerCase() === 'free');
  const trialDurationDays = freePkg?.agent_expiry_days || 7;
  const allowsEndpointTools = tierAllowsEndpointTools(subscriptionPackages, activeTier);
  const allowsFormBuilder = tierAllowsFormBuilder(subscriptionPackages, activeTier);
  const allowsOneDrive = tierAllowsOneDrive(subscriptionPackages, activeTier);
  const allowsSharePoint = tierAllowsSharePoint(subscriptionPackages, activeTier);
  const whatsappEntitled = company?.features?.whatsapp?.enabled === true;
  const whatsappAvailable = whatsappEntitled && whatsappConnection.configured;

  const getAuthHeaders = useCallback(() => {
    const token = getToken();
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  }, []);

  const loadCustomAgents = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(URL.CUSTOM_AGENTS, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch custom agents");
      const data = await response.json();
      setCustomAgents(data);
    } catch (error) {
      console.error("Error loading custom agents:", error);
    } finally {
      setLoading(false);
    }
  }, [getAuthHeaders]);

  const loadPayments = useCallback(async () => {
    try {
      const response = await fetch("/api/auth/payments", {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch payment history");
      const data = await response.json();
      setPayments(data);
    } catch (error) {
      console.error("Error loading payments:", error);
    }
  }, [getAuthHeaders]);

  const loadWhatsappConnection = useCallback(async () => {
    if (userRole !== "admin" || !whatsappEntitled) {
      setWhatsappConnection({ configured: false, phone_number: null });
      return;
    }
    try {
      const response = await fetch(URL.WHATSAPP_CONFIGURATION, {
        headers: getAuthHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || "Failed to load WhatsApp configuration");
      }
      setWhatsappConnection(data.connection || { configured: false, phone_number: null });
    } catch (error) {
      console.error("Error loading WhatsApp configuration:", error);
      setWhatsappConnection({ configured: false, phone_number: null });
    }
  }, [getAuthHeaders, userRole, whatsappEntitled]);

  const loadSubscriptionPackages = useCallback(async () => {
    try {
      const response = await fetch(URL.AUTH_SUBSCRIPTION_PACKAGES, {
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to fetch subscription packages");
      const data = await response.json();
      setSubscriptionPackages(Array.isArray(data.packages) ? data.packages : []);
    } catch (error) {
      console.error("Error loading subscription packages:", error);
    }
  }, [getAuthHeaders]);

  const getRemainingTrialDays = useCallback(() => {
    if (!company?.trial_expires_at) return 0;
    try {
      const expires = new Date(company.trial_expires_at);
      const now = new Date();
      const diffMs = expires - now;
      if (diffMs <= 0) return 0;
      return Math.ceil(diffMs / (1000 * 60 * 60 * 24));
    } catch (e) {
      return 0;
    }
  }, [company?.trial_expires_at]);

  useEffect(() => {
    setShowOnboarding(Boolean(shouldShowOnboarding));
  }, [shouldShowOnboarding]);

  const markOnboardingSeen = useCallback(async () => {
    try {
      await fetch("/api/auth/onboarding", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ option: "dismiss" }),
      });
    } catch (err) {
      console.error("Error marking onboarding as seen:", err);
    }
  }, [getAuthHeaders]);

  const closeOnboarding = async () => {
    setShowOnboarding(false);
    await markOnboardingSeen();
  };

  const handleOnboardingOption = async (option) => {
    try {
      const response = await fetch("/api/auth/onboarding", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ option }),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.message || "Failed to complete onboarding");
      }
      if (option === "upgrade") {
        localStorage.setItem("open_billing_on_load", "true");
      }
      window.location.reload();
    } catch (err) {
      await showAlert(err.message || "Failed to complete onboarding.", {
        title: "Onboarding failed",
        variant: "danger",
      });
    }
  };

  // Load user information, custom agents, and payments
  useEffect(() => {
    const fullName = (user && user.full_name) ? user.full_name : "";
    const parts = fullName.trim().split(/\s+/).filter(Boolean);
    setUserInfo({
      firstName: parts[0] || '',
      lastName: parts.slice(1).join(' ') || '',
      email: (user && user.email) ? user.email : ''
    });

    if (user?.email) {
      const activeAccount = instance.getActiveAccount();
      if (!activeAccount || activeAccount.username.toLowerCase() !== user.email.toLowerCase()) {
        const account = instance.getAllAccounts().find(
          (acc) => acc.username.toLowerCase() === user.email.toLowerCase()
        );
        if (account) {
          instance.setActiveAccount(account);
        }
      }
    }

    loadCustomAgents();
    loadPayments();
    loadSubscriptionPackages();

    if (localStorage.getItem("open_billing_on_load") === "true") {
      localStorage.removeItem("open_billing_on_load");
      setShowBillingPopup(true);
    }
  }, [user, loadCustomAgents, loadPayments, loadSubscriptionPackages, instance]);

  useEffect(() => {
    loadWhatsappConnection();
  }, [loadWhatsappConnection]);

  // Handle Stripe payment/subscription success callback verification
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const paymentStatus = params.get("payment");
    const subscriptionStatus = params.get("subscription");
    const whatsappAddonStatus = params.get("whatsapp_addon");
    const sessionId = params.get("session_id");
    const tierName = params.get("tier");

    if (whatsappAddonStatus === "success" && sessionId) {
      if (verificationStarted.current) return;
      verificationStarted.current = true;
      setShowWhatsappConfiguration(true);
      const verifyWhatsAppPayment = async () => {
        setLoading(true);
        showMessage("Verifying WhatsApp add-on payment. Please wait...", "info");
        try {
          const res = await fetch(URL.WHATSAPP_ADDON_PAYMENT_SUCCESS, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...getAuthHeaders(),
            },
            body: JSON.stringify({ session_id: sessionId }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) {
            throw new Error(data.message || "WhatsApp payment verification failed.");
          }
          showMessage("WhatsApp add-on payment verified successfully.", "success");
          loadPayments();
          loadWhatsappConnection();
          setTimeout(() => {
            window.location.reload();
          }, 1500);
        } catch (err) {
          console.error("[WhatsApp Stripe verification error]:", err);
          showMessage(
            err.message || "WhatsApp payment verification failed. Please contact support.",
            "error"
          );
        } finally {
          setLoading(false);
          window.history.replaceState({}, "", "/dashboard");
        }
      };
      verifyWhatsAppPayment();
    } else if (whatsappAddonStatus === "success") {
      setShowWhatsappConfiguration(true);
      showMessage("Stripe did not return a Checkout Session ID.", "error");
      window.history.replaceState({}, "", "/dashboard");
    } else if (whatsappAddonStatus === "cancelled") {
      setShowWhatsappConfiguration(true);
      showMessage("WhatsApp add-on checkout was cancelled.", "error");
      window.history.replaceState({}, "", "/dashboard");
    } else if ((paymentStatus === "success" || subscriptionStatus === "success") && sessionId) {
      if (verificationStarted.current) return;
      verificationStarted.current = true;

      const verifyPayment = async () => {
        setLoading(true);
        showMessage("Verifying transaction. Please wait...", "info");
        try {
          const res = await fetch("/api/auth/payment-success", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...getAuthHeaders(),
            },
            body: JSON.stringify({ session_id: sessionId }),
          });

          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || "Verification failed.");
          }

          const resData = await res.json();
          window.history.replaceState({}, "", "/dashboard");

          // Show success modal immediately
          if (resData.payment) {
            setSuccessPaymentDetails(resData.payment);
            setShowPaymentSuccessModal(true);
          } else {
            if (subscriptionStatus === "success") {
              showMessage(`Subscription verified! Your workspace has been upgraded to ${tierName ? tierName.toUpperCase() : "active"} plan.`, "success");
              setTimeout(() => {
                window.location.reload();
              }, 2000);
            } else {
              showMessage("Payment verified! Your agent has been created successfully.", "success");
            }
          }

          // Reload list of custom agents and payments in the background
          loadCustomAgents();
          loadPayments();
          
        } catch (err) {
          console.error("[Stripe verification error]:", err);
          showMessage(err.message || "Verification failed. Please contact support.", "error");
          window.history.replaceState({}, "", "/dashboard");
        } finally {
          setLoading(false);
        }
      };

      verifyPayment();
    } else if (paymentStatus === "cancelled" || subscriptionStatus === "cancelled") {
      showMessage("Payment transaction was cancelled.", "error");
      window.history.replaceState({}, "", "/dashboard");
    }
  }, [navigate, loadCustomAgents, loadPayments, loadWhatsappConnection, getAuthHeaders]);

  // Lock scroll while a dashboard modal is open.
  useEffect(() => {
    if (showKnowledgebasePopup || showWhatsappConfiguration) {
      document.body.classList.add("admin-body-lock");
      document.documentElement.classList.add("admin-body-lock");
    } else {
      document.body.classList.remove("admin-body-lock");
      document.documentElement.classList.remove("admin-body-lock");
    }
    return () => {
      document.body.classList.remove("admin-body-lock");
      document.documentElement.classList.remove("admin-body-lock");
    };
  }, [showKnowledgebasePopup, showWhatsappConfiguration]);

  if (isSuperAdmin) {
    return <Navigate to="/admin-dashboard" replace />;
  }

  const showMessage = (text, type) => {
    setMessage({ text, type });
    setTimeout(() => setMessage({ text: "", type: "" }), 5000);
  };

  // Standard agents are no longer used in this system
  const accessibleAgents = [];


  const handleAgentClick = (path) => {
    navigate(path);
  };

  const handleAdminDashboard = () => {
    navigate("/admin-dashboard");
  };

  const getFreshMicrosoftToken = async () => {
    const existingAccount = instance.getActiveAccount() || accounts[0];
    if (existingAccount) {
      try {
        const tokenResponse = await instance.acquireTokenSilent({
          ...tokenRequest,
          account: existingAccount,
        });
        return tokenResponse.accessToken;
      } catch (e) {
        console.warn("Silent Microsoft token refresh failed, using popup:", e);
      }
    }

    const result = await instance.loginPopup({
      ...loginRequest,
      prompt: "select_account",
    });
    instance.setActiveAccount(result.account);
    const tokenResponse = await instance.acquireTokenSilent({
      ...tokenRequest,
      account: result.account,
    });
    return tokenResponse.accessToken;
  };

  const handleMSLoginForAgent = async () => {
    try {
      const accessToken = await getFreshMicrosoftToken();
      setAgentForm(prev => ({ ...prev, microsoft_token: accessToken }));
      showMessage("Successfully connected to Microsoft!", "success");
    } catch (e) {
      console.error("MS Login Error:", e);
      showMessage("Failed to connect to Microsoft", "error");
    }
  };

  const syncKnowledgeBaseSource = async (kb, index) => {
    const value = (kb.value || "").trim();
    if (!agentForm.id) {
      showMessage("Save the agent before syncing its knowledge base.", "error");
      return;
    }
    if (!value) {
      showMessage("Add a knowledge base source before syncing.", "error");
      return;
    }

    setSyncingKbIndex(index);
    try {
      const isOneDrive = kb.type === "onedrive";
      const isSharePoint = kb.type === "sharepoint";
      const isMicrosoftSource = isOneDrive || isSharePoint;
      let accessToken = agentForm.microsoft_token;
      if (isMicrosoftSource) {
        accessToken = await getFreshMicrosoftToken();
        setAgentForm(prev => ({ ...prev, microsoft_token: accessToken }));
      }

      const endpoint = isSharePoint ? URL.INGEST_SHAREPOINT : isOneDrive ? URL.INGEST_ONEDRIVE : URL.INGEST_URL;
      const response = await fetch(endpoint, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify(isMicrosoftSource ? {
          agent_id: agentForm.id,
          folder_link: value,
          access_token: accessToken,
        } : {
          agent_id: agentForm.id,
          url: value,
          crawl: webCrawlPayload(kb),
        }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.message || data.error || "Failed to start knowledge base sync");
      }

      showMessage("Knowledge base sync started. New documents will be available after ingestion completes.", "success");
      pollIngestionStatus(agentForm.id);
    } catch (error) {
      console.error("KB sync error:", error);
      showMessage(error.message || "Failed to sync knowledge base", "error");
    } finally {
      setSyncingKbIndex(null);
    }
  };

  const resetAgentForm = () => {
    setAgentForm({
      name: "",
      description: "",
      tools: [],
      knowledgeBase: [{ type: 'onedrive', value: '' }],
      kbRoutingProfile: createEmptyRoutingProfile(),
      microsoft_token: "",
      whatsappEnabled: false,
    });
    setHasKbSelected(false);
    setHasApiSelected(false);
    setHasMcpSelected(false);
    setHasDatabaseSelected(false);
    setHasFormSelected(false);
    setValidationErrors({});
  };

  const handleCheckboxChange = (type, checked) => {
    if (validationErrors.toolsSelection) {
      setValidationErrors(prev => {
        const copy = { ...prev };
        delete copy.toolsSelection;
        return copy;
      });
    }
    if (type === 'kb') {
      setHasKbSelected(checked);
    } else if (type === 'api') {
      setHasApiSelected(checked);
      if (checked) {
        if (!agentForm.tools.some(isRestApiToolConfig)) {
          setAgentForm(prev => ({
            ...prev,
            tools: [...prev.tools, {
              type: 'api',
              config: { kind: 'rest', url: '', method: 'GET', format: 'JSON', parameters: [], testPrompt: '', ...resetApiVerification }
            }]
          }));
        }
      } else {
        setAgentForm(prev => ({ ...prev, tools: prev.tools.filter(t => !isRestApiToolConfig(t)) }));
      }
    } else if (type === 'mcp') {
      setHasMcpSelected(checked);
      if (checked) {
        if (!agentForm.tools.some(isMcpToolConfig)) {
          setAgentForm(prev => ({
            ...prev,
            tools: [...prev.tools, {
              type: 'api',
              config: {
                kind: 'mcp',
                name: '',
                url: '',
                authType: 'none',
                authToken: '',
                authHeaderName: '',
                allowedTools: [],
                mcpTools: [],
                testPrompt: '',
                ...resetApiVerification
              }
            }]
          }));
        }
      } else {
        setAgentForm(prev => ({ ...prev, tools: prev.tools.filter(t => !isMcpToolConfig(t)) }));
      }
    } else if (type === 'database') {
      setHasDatabaseSelected(checked);
      if (checked) {
        if (!agentForm.tools.some(isDatabaseToolConfig)) {
          setAgentForm(prev => ({
            ...prev,
            tools: [...prev.tools, {
              type: 'database',
              config: createDefaultDatabaseToolConfig()
            }]
          }));
        }
      } else {
        setAgentForm(prev => ({ ...prev, tools: prev.tools.filter(t => !isDatabaseToolConfig(t)) }));
      }
    } else if (type === 'form') {
      setHasFormSelected(checked);
      if (checked) {
        if (!agentForm.tools.some(t => t.type === 'form')) {
          setAgentForm(prev => ({
            ...prev,
            tools: [...prev.tools, {
              type: 'form',
              config: {
                fields: [], submitAction: 'email', submitActions: ['email'],
                apiEndpoint: '', triggerDescription: '', triggerKeywords: '',
                confirmationMessage: 'Please complete the form below.'
              }
            }]
          }));
        }
      } else {
        setAgentForm(prev => ({ ...prev, tools: prev.tools.filter(t => t.type !== 'form') }));
      }
    }
  };

  const toggleKnowledgebasePopup = () => {
    if (showKnowledgebasePopup) {
      if (!agentForm.id) {
        // Save draft
        const draft = {
          agentForm,
          hasKbSelected,
          hasApiSelected,
          hasMcpSelected,
          hasDatabaseSelected,
          hasFormSelected
        };
        localStorage.setItem("agent_creation_draft", JSON.stringify(draft));
      } else {
        resetAgentForm();
      }
      setShowKnowledgebasePopup(false);
    } else {
      const limit = getTierAgentLimit(subscriptionPackages, activeTier);
      if (customAgents.length >= limit && !agentForm.id) {
        const limitLabel = formatAgentLimitDisplay(subscriptionPackages, activeTier);
        showMessage(`Agent creation limit reached for your active ${activeTier.toUpperCase()} plan (Limit: ${limitLabel}). Please upgrade your plan.`, "error");
        setShowBillingPopup(true);
        return;
      }
      if (!agentForm.id) {
        // Load draft if it exists
        const savedDraft = localStorage.getItem("agent_creation_draft");
        if (savedDraft) {
          try {
            const parsed = JSON.parse(savedDraft);
            setAgentForm({
              ...parsed.agentForm,
              whatsappEnabled: Boolean(parsed.agentForm?.whatsappEnabled),
            });
            setHasKbSelected(parsed.hasKbSelected);
            setHasApiSelected(parsed.hasApiSelected);
            setHasMcpSelected(Boolean(parsed.hasMcpSelected || parsed.agentForm?.tools?.some(isMcpToolConfig)));
            setHasDatabaseSelected(Boolean(parsed.hasDatabaseSelected || parsed.agentForm?.tools?.some(isDatabaseToolConfig)));
            setHasFormSelected(parsed.hasFormSelected);
          } catch (e) {
            console.error("Error loading agent creation draft:", e);
          }
        }
      }
      setShowKnowledgebasePopup(true);
    }
  };

  const openManageAgentsForm = () => {
    toggleKnowledgebasePopup();
  };

  const handleClearDraft = async () => {
    const confirmed = await showConfirm(
      "All form details in the current agent draft will be reset.",
      {
        title: "Clear current draft?",
        confirmLabel: "Clear draft",
        variant: "danger",
      }
    );
    if (!confirmed) return;

    localStorage.removeItem("agent_creation_draft");
    resetAgentForm();
    showMessage("Draft cleared successfully", "success");
  };



  const removeTool = (index) => {
    setAgentForm(prev => {
      const nextTools = prev.tools.filter((_, i) => i !== index);
      const stillHasApi = nextTools.some(isRestApiToolConfig);
      const stillHasMcp = nextTools.some(isMcpToolConfig);
      const stillHasDatabase = nextTools.some(isDatabaseToolConfig);
      const stillHasForm = nextTools.some(t => t.type === 'form');
      setHasApiSelected(stillHasApi);
      setHasMcpSelected(stillHasMcp);
      setHasDatabaseSelected(stillHasDatabase);
      setHasFormSelected(stillHasForm);
      return { ...prev, tools: nextTools };
    });
  };

  const addApiTool = () => {
    setAgentForm(prev => ({
      ...prev,
      tools: [...prev.tools, {
        type: 'api',
        config: { url: '', method: 'GET', format: 'JSON', parameters: [], testPrompt: '', ...resetApiVerification }
      }]
    }));
    setHasApiSelected(true);
  };

  const addFormTool = () => {
    setAgentForm(prev => ({
      ...prev,
      tools: [...prev.tools, {
        type: 'form',
        config: {
          fields: [], submitAction: 'email', submitActions: ['email'],
          apiEndpoint: '', triggerDescription: '', triggerKeywords: '',
          confirmationMessage: 'Please complete the form below.'
        }
      }]
    }));
    setHasFormSelected(true);
  };

  const addDatabaseTool = () => {
    const databaseToolCount = agentForm.tools.filter(isDatabaseToolConfig).length;
    if (databaseToolCount >= MAX_DATABASE_PROFILES_PER_AGENT) {
      showMessage(`Each agent can have up to ${MAX_DATABASE_PROFILES_PER_AGENT} database profiles.`, "error");
      return;
    }
    setAgentForm(prev => ({
      ...prev,
      tools: [...prev.tools, {
        type: 'database',
        config: createDefaultDatabaseToolConfig()
      }]
    }));
    setHasDatabaseSelected(true);
  };

  const updateToolConfig = (index, newConfig) => {
    setAgentForm(prev => {
      const newTools = [...prev.tools];
      newTools[index].config = { ...newTools[index].config, ...newConfig };
      return { ...prev, tools: newTools };
    });
    if (newConfig.hasOwnProperty('url') && (validationErrors.api || validationErrors.mcp)) {
      const errorKey = isMcpToolConfig(agentForm.tools[index]) ? 'mcp' : 'api';
      setValidationErrors(prev => {
        const next = { ...prev };
        delete next[errorKey];
        return next;
      });
    }
    if ((newConfig.hasOwnProperty('host') || newConfig.hasOwnProperty('database') || newConfig.hasOwnProperty('connectionUri')) && validationErrors.database) {
      setValidationErrors(prev => {
        const next = { ...prev };
        delete next.database;
        return next;
      });
    }
    if (newConfig.hasOwnProperty('triggerDescription') && validationErrors.formTrigger) {
      setValidationErrors(prev => {
        const next = { ...prev };
        delete next.formTrigger;
        return next;
      });
    }
  };

  const handleVerifyApiTool = async (toolIndex) => {
    const tool = agentForm.tools[toolIndex];
    const config = tool?.config || {};
    const kind = String(config.kind || "rest").toLowerCase();
    if (!String(config.url || "").trim()) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        verificationMessage: kind === "mcp" ? "Enter an MCP server URL before verifying." : "Enter an API endpoint URL before verifying."
      });
      return;
    }

    if (kind !== "mcp" && !String(config.testPrompt || "").trim()) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        verificationMessage: "Enter a sample user request before verifying."
      });
      return;
    }

    updateToolConfig(toolIndex, {
      verified: false,
      verifiedSignature: "",
      verificationMessage: "Verifying endpoint...",
      verificationResult: null
    });

    try {
      const mcpAuth = normalizeMcpAuthConfig(config);
      const endpointPayload = kind === "mcp" ? {
        kind: "mcp",
        name: config.name || "",
        url: config.url,
        transport: config.transport || "streamable_http",
        auth: {
          type: mcpAuth.authType,
          token: mcpAuth.authToken,
          headerName: mcpAuth.authHeaderName
        },
        allowed_tools: config.allowedTools || []
      } : {
        url: config.url,
        method: config.method || "GET",
        format: config.format || "JSON",
        parameters: config.parameters || []
      };
      const response = await fetch(URL.API_TOOL_VERIFY, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          type: kind,
          endpoint: endpointPayload,
          test_prompt: config.testPrompt || ""
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        updateToolConfig(toolIndex, {
          ...resetApiVerification,
          verificationMessage: data.message || data.error || `Verification failed${data.status_code ? ` with status ${data.status_code}` : ""}.`,
          verificationResult: data
        });
        return;
      }

      const verifiedMcpTools = kind === "mcp"
        ? preserveMcpToolRoutingProfiles(config.mcpTools, data.tools || [])
        : config.mcpTools;
      updateToolConfig(toolIndex, {
        verified: true,
        verifiedSignature: apiToolSignature({
          ...config,
          mcpTools: verifiedMcpTools || []
        }),
        verificationMessage: kind === "mcp" ? `Verified MCP server with ${(data.tools || []).length} tool(s).` : `Verified successfully with status ${data.status_code}.`,
        mcpTools: verifiedMcpTools,
        verificationResult: data
      });
    } catch (error) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        verificationMessage: error.message || "Verification failed.",
      });
    }
  };

  const handleDetectMcpTools = async (toolIndex) => {
    const tool = agentForm.tools[toolIndex];
    const config = tool?.config || {};
    if (!String(config.url || "").trim()) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        mcpTools: [],
        allowedTools: [],
        verificationMessage: "Enter an MCP server URL before detecting tools."
      });
      return;
    }

    updateToolConfig(toolIndex, {
      verified: false,
      verifiedSignature: "",
      verificationMessage: "Detecting MCP tools...",
      verificationResult: null
    });

    try {
      const mcpAuth = normalizeMcpAuthConfig(config);
      const response = await fetch(URL.API_TOOL_VERIFY, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          type: "mcp",
          endpoint: {
            kind: "mcp",
            name: config.name || "",
            url: config.url,
            auth: {
              type: mcpAuth.authType,
              token: mcpAuth.authToken,
              headerName: mcpAuth.authHeaderName
            }
          }
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        updateToolConfig(toolIndex, {
          ...resetApiVerification,
          mcpTools: [],
          allowedTools: [],
          verificationMessage: data.message || data.error || "Could not detect MCP tools.",
          verificationResult: data
        });
        return;
      }

      const detectedTools = preserveMcpToolRoutingProfiles(
        config.mcpTools,
        Array.isArray(data.tools) ? data.tools.filter(item => item?.name) : []
      );
      const detectedNames = detectedTools.map(item => String(item.name).trim()).filter(Boolean);
      const existingAllowed = Array.isArray(config.allowedTools)
        ? config.allowedTools.map(item => String(item).trim()).filter(Boolean)
        : String(config.allowedTools || "").split(",").map(item => item.trim()).filter(Boolean);
      const preservedAllowed = existingAllowed.filter(name => detectedNames.includes(name));

      updateToolConfig(toolIndex, {
        verified: true,
        verifiedSignature: mcpToolSignature(config),
        verificationMessage: detectedNames.length ? `Detected ${detectedNames.length} MCP tool(s).` : "No MCP tools were exposed by this server.",
        verificationResult: data,
        mcpTools: detectedTools,
        allowedTools: preservedAllowed.length ? preservedAllowed : detectedNames
      });
    } catch (error) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        mcpTools: [],
        allowedTools: [],
        verificationMessage: error.message || "Could not detect MCP tools.",
      });
    }
  };

  const toggleMcpAllowedTool = (toolIndex, toolName, checked) => {
    const config = agentForm.tools[toolIndex]?.config || {};
    const current = Array.isArray(config.allowedTools)
      ? config.allowedTools.map(item => String(item).trim()).filter(Boolean)
      : String(config.allowedTools || "").split(",").map(item => item.trim()).filter(Boolean);
    const next = checked
      ? Array.from(new Set([...current, toolName]))
      : current.filter(item => item !== toolName);
    updateToolConfig(toolIndex, { allowedTools: next });
  };

  const setMcpAllowedTools = (toolIndex, toolNames) => {
    updateToolConfig(toolIndex, { allowedTools: toolNames });
  };

  const updateMcpToolRoutingProfile = (toolIndex, toolName, routingProfile) => {
    const mcpTools = (agentForm.tools[toolIndex]?.config?.mcpTools || []).map(mcpTool => (
      mcpTool.name === toolName
        ? { ...mcpTool, routing_profile: routingProfilePayload(routingProfile) }
        : mcpTool
    ));
    updateToolConfig(toolIndex, { mcpTools });
  };

  const buildDatabaseProfilePayload = (config = {}) => ({
    kind: "database",
    engine: config.engine || "postgres",
    name: config.name || "",
    purpose: config.purpose || "",
    routing_profile: routingProfilePayload(config.routingProfile || config.routing_profile),
    connectionUri: config.useConnectionUri ? (config.connectionUri || config.connectionUriSecret || "") : "",
    connectionUriSecret: config.connectionUriSecret || "",
    host: config.useConnectionUri ? "" : (config.host || ""),
    port: config.useConnectionUri ? "" : (config.port || "5432"),
    database: config.useConnectionUri ? "" : (config.database || ""),
    filePath: config.filePath || "",
    serviceName: config.serviceName || "",
    username: config.useConnectionUri ? "" : (config.username || ""),
    password: config.useConnectionUri ? "" : (config.password || config.passwordSecret || ""),
    passwordSecret: config.passwordSecret || "",
    sslmode: config.sslmode || "prefer",
    driver: config.driver || "",
    dsn: config.dsn || "",
    allowedSchemas: config.selectedSchemas || [],
    allowedTables: config.selectedTables || [],
    allowedColumns: config.selectedColumns || {},
    rolePermissionsDetected: Boolean(config.rolePermissionsDetected),
    ...(config.rolePermissionsDetected ? { roleAllowedOperations: config.roleAllowedOperations || [] } : {}),
    allowedOperations: config.allowedOperations || [],
    permissionMode: config.permissionMode || "read_only",
    schemaMetadata: config.detectedSchemas || [],
    limits: {
      maxRows: clampNumber(config.limits?.maxRows ?? config.limits?.max_rows, 100, 1, 1000),
      timeoutSeconds: clampNumber(config.limits?.timeoutSeconds ?? config.limits?.timeout_seconds, 15, 1, 120),
      maxResultBytes: clampNumber(config.limits?.maxResultBytes ?? config.limits?.max_result_bytes, 12000, 1000, 200000),
      maxAffectedRows: clampNumber(config.limits?.maxAffectedRows ?? config.limits?.max_affected_rows, 10, 1, 1000),
    }
  });

  const handleTestDatabaseConnection = async (toolIndex) => {
    const tool = agentForm.tools[toolIndex];
    const config = tool?.config || {};
    const engine = String(config.engine || "postgres").toLowerCase();
    const hasConnectionUri = String(config.connectionUri || config.connectionUriSecret || "").trim();
    const hasFilePath = String(config.filePath || config.database || "").trim();
    const hasDsn = String(config.dsn || "").trim();
    const missingDirectFields = !config.useConnectionUri && (
      engine === "sqlite"
        ? !hasFilePath
        : engine === "odbc"
          ? !hasDsn
          : (!String(config.host || "").trim() || !String(config.database || "").trim() || !String(config.username || "").trim())
    );
    if ((config.useConnectionUri && !hasConnectionUri) || missingDirectFields) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        detectedSchemas: [],
        selectedSchemas: [],
        selectedTables: [],
        verificationMessage: config.useConnectionUri
          ? "Enter a database connection string before testing."
          : engine === "sqlite"
            ? "Enter a SQLite database file path before testing."
            : engine === "odbc"
              ? "Enter an ODBC DSN before testing."
              : "Enter host, database name, and DB role username before testing."
      });
      return;
    }

    updateToolConfig(toolIndex, {
      verified: false,
      verifiedSignature: "",
      verificationMessage: "Testing database connection...",
      verificationResult: null
    });

    try {
      const response = await fetch(URL.API_TOOL_VERIFY, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          type: "database",
          endpoint: buildDatabaseProfilePayload(config)
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        updateToolConfig(toolIndex, {
          ...resetApiVerification,
          detectedSchemas: [],
          selectedSchemas: [],
          selectedTables: [],
          verificationMessage: data.message || data.error || "Could not connect to this database.",
          verificationResult: data
        });
        return;
      }

      const schemas = Array.isArray(data.schemas) ? data.schemas : [];
      const schemaNames = schemas.map(schema => schema.name).filter(Boolean);
      const existingSchemas = Array.isArray(config.selectedSchemas) ? config.selectedSchemas : [];
      const selectedSchemas = existingSchemas.filter(name => schemaNames.includes(name));
      const tableRefs = new Set(
        schemas.flatMap(schema => (schema.tables || []).map(table => `${schema.name}.${table.name}`))
      );
      const selectedTables = (Array.isArray(config.selectedTables) ? config.selectedTables : [])
        .filter(ref => tableRefs.has(ref));

      updateToolConfig(toolIndex, {
        verified: true,
        verifiedSignature: databaseToolSignature(config),
        verificationMessage: data.message || `Connected. Detected ${schemas.length} schema(s) visible to this DB role.`,
        verificationResult: data,
        rolePermissionsDetected: Boolean(data.role_permissions_detected),
        accessModeOperations: Array.isArray(data.access_mode_operations) ? data.access_mode_operations : [],
        roleAllowedOperations: Array.isArray(data.role_allowed_operations) ? data.role_allowed_operations : null,
        allowedOperations: Array.isArray(data.allowed_operations) ? data.allowed_operations : [],
        detectedSchemas: schemas,
        selectedSchemas,
        selectedTables,
      });
    } catch (error) {
      updateToolConfig(toolIndex, {
        ...resetApiVerification,
        detectedSchemas: [],
        selectedSchemas: [],
        selectedTables: [],
        verificationMessage: error.message || "Could not connect to this database.",
      });
    }
  };

  const pollIngestionStatus = async (agentId) => {
    if (!agentId) return;
    const maxAttempts = 12;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, attempt === 0 ? 1500 : 3000));
      try {
        const response = await fetch(`${URL.INGESTION_STATUS}?agent_id=${encodeURIComponent(agentId)}`, {
          headers: getAuthHeaders(),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.status || data.status === "idle" || data.status === "pending" || data.status === "running") {
          continue;
        }
        if (data.status === "completed") {
          showMessage(`Knowledge base sync completed: ${data.docs_indexed || 0} document(s), ${data.chunks_indexed || 0} chunk(s).`, "success");
          return;
        }
        if (data.status === "failed") {
          showMessage(data.error || "Knowledge base sync failed.", "error");
          return;
        }
      } catch (error) {
        console.error("Failed to poll ingestion status", error);
      }
    }
  };

  const toggleDatabaseSchema = (toolIndex, schemaName, checked) => {
    const config = agentForm.tools[toolIndex]?.config || {};
    const currentSchemas = Array.isArray(config.selectedSchemas) ? config.selectedSchemas : [];
    const nextSchemas = checked
      ? Array.from(new Set([...currentSchemas, schemaName]))
      : currentSchemas.filter(name => name !== schemaName);
    const nextTables = (Array.isArray(config.selectedTables) ? config.selectedTables : [])
      .filter(ref => checked || !ref.startsWith(`${schemaName}.`));
    const nextColumns = { ...(config.selectedColumns || {}) };
    if (!checked) {
      Object.keys(nextColumns).forEach(ref => {
        if (ref.startsWith(`${schemaName}.`)) delete nextColumns[ref];
      });
    }
    updateToolConfig(toolIndex, { selectedSchemas: nextSchemas, selectedTables: nextTables, selectedColumns: nextColumns });
  };

  const toggleDatabaseTable = (toolIndex, schemaName, tableName, checked) => {
    const config = agentForm.tools[toolIndex]?.config || {};
    const ref = `${schemaName}.${tableName}`;
    const currentTables = Array.isArray(config.selectedTables) ? config.selectedTables : [];
    const nextTables = checked
      ? Array.from(new Set([...currentTables, ref]))
      : currentTables.filter(item => item !== ref);
    const currentSchemas = Array.isArray(config.selectedSchemas) ? config.selectedSchemas : [];
    const nextSchemas = currentSchemas.includes(schemaName) ? currentSchemas : [...currentSchemas, schemaName];
    const nextColumns = { ...(config.selectedColumns || {}) };
    if (!checked) {
      delete nextColumns[ref];
    }
    updateToolConfig(toolIndex, { selectedSchemas: nextSchemas, selectedTables: nextTables, selectedColumns: nextColumns });
  };

  const toggleDatabaseColumn = (toolIndex, tableRef, columnName, checked) => {
    const config = agentForm.tools[toolIndex]?.config || {};
    const currentColumns = config.selectedColumns || {};
    const tableColumns = Array.isArray(currentColumns[tableRef]) ? currentColumns[tableRef] : [];
    const nextTableColumns = checked
      ? Array.from(new Set([...tableColumns, columnName]))
      : tableColumns.filter(item => item !== columnName);
    const nextColumns = { ...currentColumns };
    if (nextTableColumns.length) {
      nextColumns[tableRef] = nextTableColumns;
    } else {
      delete nextColumns[tableRef];
    }
    updateToolConfig(toolIndex, { selectedColumns: nextColumns });
  };

  const addParameter = (toolIndex) => {
    const newParams = [...(agentForm.tools[toolIndex].config.parameters || []), { key: '', value: '', type: 'string' }];
    updateToolConfig(toolIndex, { parameters: newParams, ...resetApiVerification });
  };

  const updateParameter = (toolIndex, paramIndex, field, value) => {
    const newParams = [...agentForm.tools[toolIndex].config.parameters];
    newParams[paramIndex][field] = value;
    updateToolConfig(toolIndex, { parameters: newParams, ...resetApiVerification });
  };

  const removeParameter = (toolIndex, paramIndex) => {
    const newParams = agentForm.tools[toolIndex].config.parameters.filter((_, i) => i !== paramIndex);
    updateToolConfig(toolIndex, { parameters: newParams, ...resetApiVerification });
  };

  const addField = (toolIndex) => {
    const newFields = [
      ...(agentForm.tools[toolIndex].config.fields || []),
      {
        label: '',
        type: 'Text',
        inputType: 'text',
        dataType: 'string',
        required: 'Optional',
        options: []
      }
    ];
    updateToolConfig(toolIndex, { fields: newFields });
  };

  const updateField = (toolIndex, fieldIndex, prop, value) => {
    const newFields = [...agentForm.tools[toolIndex].config.fields];
    newFields[fieldIndex][prop] = value;
    updateToolConfig(toolIndex, { fields: newFields });
  };

  const removeField = (toolIndex, fieldIndex) => {
    const newFields = agentForm.tools[toolIndex].config.fields.filter((_, i) => i !== fieldIndex);
    updateToolConfig(toolIndex, { fields: newFields });
  };

  const addKBLink = () => {
    setAgentForm(prev => ({ ...prev, knowledgeBase: [...prev.knowledgeBase, { type: 'onedrive', value: '' }] }));
  };

  const updateKBLink = (index, field, value) => {
    setAgentForm(prev => {
      const newKB = [...prev.knowledgeBase];
      newKB[index][field] = value;
      if (field === "type" && value === "website" && !newKB[index].crawl) {
        newKB[index].crawl = { ...DEFAULT_WEB_CRAWL_SETTINGS };
      }
      return { ...prev, knowledgeBase: newKB };
    });
    if (field === 'value' && value.trim() && validationErrors.kb) {
      setValidationErrors(prev => {
        const next = { ...prev };
        delete next.kb;
        return next;
      });
    }
  };
  const removeKBLink = (index) => {
    setAgentForm(prev => ({ ...prev, knowledgeBase: prev.knowledgeBase.filter((_, i) => i !== index) }));
  };

  // ── Stripe Checkout Redirect ──────────────────────────────────────────────
  // The backend creates a Stripe Checkout Session using the secret key (STRIPE_SECRET_KEY)
  // and returns a hosted checkout URL. The secret key never touches the frontend.
  const redirectToStripe = async ({ email, tier }) => {
    try {
      const res = await fetch("/api/auth/create-checkout-session", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders()
        },
        body: JSON.stringify({
          email: email || "",
          tier
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || "Failed to create Stripe checkout session");
      }

      const { url } = await res.json();
      if (!url) throw new Error("No checkout URL returned from server");

      window.location.href = url; // ← browser navigates to Stripe hosted checkout
    } catch (err) {
      console.error("[Stripe] Checkout error:", err);
      showMessage(err.message || "Payment redirect failed. Please try again.", "error");
    }
  };
  // ─────────────────────────────────────────────────────────────────────────

  const handleSaveAgent = async () => {
    const errors = {};
    if (!agentForm.name.trim()) {
      errors.name = "Agent Name is required to create the agent.";
    }

    if (!hasKbSelected && !hasApiSelected && !hasMcpSelected && !hasDatabaseSelected && !hasFormSelected) {
      errors.toolsSelection = "Please select and configure at least one tool (Knowledge Base is recommended).";
    }

    const apiTools = agentForm.tools.filter(isRestApiToolConfig);
    const mcpTools = agentForm.tools.filter(isMcpToolConfig);
    const databaseTools = agentForm.tools.filter(isDatabaseToolConfig);
    const formTools = agentForm.tools.filter(tool => tool.type === "form");
    const kbResources = agentForm.knowledgeBase.filter(kb => (kb.value || "").trim());

    if (hasKbSelected && !kbResources.length) {
      errors.kb = "Knowledge Base URL, OneDrive path, or SharePoint path is required when Knowledge Base is enabled.";
    }

    if (hasApiSelected) {
      const hasValidUrl = apiTools.some(tool => (tool.config.url || "").trim());
      if (!hasValidUrl) {
        errors.api = "API Endpoint URL is required when API Endpoint Tool is enabled.";
      }
    }

    if (hasMcpSelected) {
      const hasValidMcpUrl = mcpTools.some(tool => (tool.config.url || "").trim());
      if (!hasValidMcpUrl) {
        errors.mcp = "MCP Server URL is required when MCP Tool is enabled.";
      } else {
        const needsToolDetection = mcpTools.some(tool => !Array.isArray(tool.config.mcpTools) || !tool.config.mcpTools.length);
        const missingAllowedTools = mcpTools.some(tool => {
          const allowedTools = Array.isArray(tool.config.allowedTools)
            ? tool.config.allowedTools
            : String(tool.config.allowedTools || "").split(",").map(item => item.trim()).filter(Boolean);
          return !allowedTools.length;
        });
        if (needsToolDetection) {
          errors.mcp = "Detect MCP tools before assigning the MCP server to the agent.";
        } else if (missingAllowedTools) {
          errors.mcp = "Select at least one allowed MCP tool for each MCP server.";
        }
      }
    }

    if (hasDatabaseSelected) {
      if (databaseTools.length > MAX_DATABASE_PROFILES_PER_AGENT) {
        errors.database = `Each agent can have up to ${MAX_DATABASE_PROFILES_PER_AGENT} database profiles.`;
      }
      const invalidDatabase = databaseTools.some(tool => {
        const config = tool.config || {};
        const engine = String(config.engine || "postgres").toLowerCase();
        if (config.useConnectionUri) return !String(config.connectionUri || config.connectionUriSecret || "").trim();
        if (engine === "sqlite") return !String(config.filePath || config.database || "").trim();
        if (engine === "odbc") return !String(config.dsn || "").trim();
        return !String(config.host || "").trim() || !String(config.database || "").trim() || !String(config.username || "").trim();
      });
      if (!errors.database && invalidDatabase) {
        errors.database = "Complete the required connection fields for each Database Tool profile.";
      } else if (!errors.database) {
        const missingSchemaScope = databaseTools.some(tool => !(tool.config.selectedSchemas || []).length);
        if (missingSchemaScope) {
          errors.database = "Test the database connection and select at least one schema for the agent.";
        } else {
          const writeProfilesWithoutTables = databaseTools.some(tool => (
            String(tool.config.permissionMode || "read_only") !== "read_only" &&
            !(tool.config.selectedTables || []).length
          ));
          const writeProfilesWithoutColumns = databaseTools.some(tool => {
            const mode = String(tool.config.permissionMode || "read_only");
            if (!databaseModeAllowsColumnWrites(mode)) {
              return false;
            }
            const selectedColumns = tool.config.selectedColumns || {};
            return !(tool.config.selectedTables || []).some(ref => (selectedColumns[ref] || []).length);
          });
          if (writeProfilesWithoutTables) {
            errors.database = "Select specific tables before enabling database write permissions.";
          } else if (writeProfilesWithoutColumns) {
            errors.database = "Select writable columns for insert/update database permission modes.";
          }
        }
      }
    }

    if (hasFormSelected) {
      const hasValidTrigger = formTools.some(tool => (tool.config.triggerDescription || "").trim());
      if (!hasValidTrigger) {
        errors.formTrigger = "Trigger Description is required when Form Builder is enabled.";
      }
    }

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      showMessage(Object.values(errors)[0], "error");
      return;
    }

    setValidationErrors({});
    const unverifiedApiTool = apiTools.find(tool => (
      !tool.config.verified || tool.config.verifiedSignature !== apiToolSignature(tool.config)
    ));
    if (unverifiedApiTool) {
      showMessage("Verify each API endpoint before assigning it to the agent.", "error");
      return;
    }
    const mcpMissingAllowedTool = mcpTools.some(tool => !(tool.config.allowedTools || []).length);
    if (mcpMissingAllowedTool) {
      showMessage("Select at least one allowed MCP tool for each MCP server.", "error");
      return;
    }
    const unverifiedDatabaseTool = databaseTools.find(tool => (
      !tool.config.verified || tool.config.verifiedSignature !== databaseToolSignature(tool.config)
    ));
    if (unverifiedDatabaseTool) {
      showMessage("Test each database connection before assigning it to the agent.", "error");
      return;
    }
    const formMissingApiEndpoint = formTools.some(tool => {
      const actions = getFormSubmitActions(tool.config);
      return actions.includes("api") && !(tool.config.apiEndpoint || "").trim();
    });
    if (formMissingApiEndpoint) {
      showMessage("Add an API endpoint for each form configured to POST submissions.", "error");
      return;
    }
    const formMissingTrigger = formTools.some(tool => !(tool.config.triggerDescription || "").trim());
    if (formMissingTrigger) {
      showMessage("Add a trigger description for each form builder tool.", "error");
      return;
    }
    const triggerDescriptions = formTools.map(tool => (tool.config.triggerDescription || "").trim().toLowerCase()).filter(Boolean);
    if (new Set(triggerDescriptions).size !== triggerDescriptions.length) {
      showMessage("Each form builder trigger description must be distinct.", "error");
      return;
    }
    setLoading(true);
    try {
      const toList = (value) => {
        if (Array.isArray(value)) return value.map(item => String(item).trim()).filter(Boolean);
        return String(value || "").split(",").map(item => item.trim()).filter(Boolean);
      };
      const isEdit = !!agentForm.id;
      const url = isEdit ? `${URL.CUSTOM_AGENTS}/${agentForm.id}` : URL.AGENT_CONFIGURATIONS;
      const method = isEdit ? "PATCH" : "POST";
      const webUrls = kbResources.filter(kb => kb.type === "website").map(kb => kb.value.trim());
      const webSources = kbResources
        .filter(kb => kb.type === "website")
        .map(kb => ({ url: kb.value.trim(), ...webCrawlPayload(kb) }));
      const oneDriveFolders = kbResources.filter(kb => kb.type === "onedrive").map(kb => kb.value.trim());
      const sharePointFolders = kbResources.filter(kb => kb.type === "sharepoint").map(kb => kb.value.trim());
      const hasMicrosoftKbSource = oneDriveFolders.length > 0 || sharePointFolders.length > 0;
      let microsoftTokenForRequest = agentForm.microsoft_token;
      if (hasMicrosoftKbSource) {
        try {
          microsoftTokenForRequest = await getFreshMicrosoftToken();
          setAgentForm(prev => ({ ...prev, microsoft_token: microsoftTokenForRequest }));
        } catch (tokenError) {
          console.error("Failed to get Microsoft token", tokenError);
          showMessage("Connect to Microsoft 365 before saving OneDrive or SharePoint knowledge sources.", "error");
          return;
        }
      }
      const mcpServers = mcpTools.map(tool => {
        const mcpAuth = normalizeMcpAuthConfig(tool.config);
        return {
          kind: 'mcp',
          name: tool.config.name || "",
          url: tool.config.url || "",
          authType: mcpAuth.authType,
          authToken: mcpAuth.authToken,
          authHeaderName: mcpAuth.authHeaderName,
          auth: {
            type: mcpAuth.authType,
            token: mcpAuth.authToken,
            headerName: mcpAuth.authHeaderName
          },
          routing_profile: routingProfilePayload(tool.config.routingProfile || tool.config.routing_profile),
          allowedTools: toList(tool.config.allowedTools),
          mcpTools: Array.isArray(tool.config.mcpTools) ? tool.config.mcpTools : []
        };
      });
      const createPayload = {
        agent_name: agentForm.name,
        description: agentForm.description || "",
        whatsapp_enabled: Boolean(agentForm.whatsappEnabled),
        tools: [
          ...(apiTools.length ? ["api"] : []),
          ...(mcpTools.length ? ["mcp"] : []),
          ...(databaseTools.length ? ["database"] : []),
          ...(formTools.length ? ["form_handler"] : []),
          ...(kbResources.length ? ["knowledge_base"] : [])
        ],
        api: apiTools.length ? {
          api_endpoints: apiTools.map(tool => ({
            url: tool.config.url,
            method: tool.config.method,
            format: tool.config.format,
            parameters: tool.config.parameters || [],
            routing_profile: routingProfilePayload(tool.config.routingProfile || tool.config.routing_profile)
          })),
          mcp_servers: mcpTools.map(tool => {
            const mcpAuth = normalizeMcpAuthConfig(tool.config);
            return {
              name: tool.config.name || "",
              url: tool.config.url,
              transport: tool.config.transport || "streamable_http",
              auth: {
                type: mcpAuth.authType,
                token: mcpAuth.authToken,
                headerName: mcpAuth.authHeaderName
              },
              routing_profile: routingProfilePayload(tool.config.routingProfile || tool.config.routing_profile),
              allowed_tools: tool.config.allowedTools || [],
              tools: tool.config.mcpTools || tool.config.tools || []
            };
          }),
          req_type: apiTools[0]?.config?.method || "GET"
        } : undefined,
        knowledgeBase: kbResources.length ? {
          web_urls: webUrls,
          web_sources: webSources,
          onedrive_folder_id: oneDriveFolders[0] || "",
          onedrive_folder_ids: oneDriveFolders,
          sharepoint_folder_id: sharePointFolders[0] || "",
          sharepoint_folder_ids: sharePointFolders,
          routing_profile: routingProfilePayload(agentForm.kbRoutingProfile),
          microsoft_token: microsoftTokenForRequest
        } : undefined,
        mcp: mcpServers.length ? {
          servers: mcpServers
        } : undefined,
        database: databaseTools.length ? {
          profiles: databaseTools.map(tool => buildDatabaseProfilePayload(tool.config))
        } : undefined,
        form_handlers: formTools.length ? formTools.map(tool => ({
          fields: tool.config.fields || [],
          submitAction: getFormSubmitActions(tool.config)[0] || "email",
          submitActions: getFormSubmitActions(tool.config),
          apiEndpoint: tool.config.apiEndpoint || "",
          triggerDescription: tool.config.triggerDescription || "",
          triggerKeywords: toList(tool.config.triggerKeywords),
          routing_profile: routingProfilePayload(tool.config.routingProfile || tool.config.routing_profile),
          confirmationMessage: tool.config.confirmationMessage || "Please complete the form below."
        })) : undefined
      };

      const agentPayload = isEdit ? {
        name: agentForm.name,
        description: agentForm.description || "",
        whatsapp_enabled: Boolean(agentForm.whatsappEnabled),
        microsoft_token: microsoftTokenForRequest,
        knowledgeBase: createPayload.knowledgeBase,
        tools: createPayload.tools,
        api: createPayload.api,
        mcp: createPayload.mcp,
        database: createPayload.database,
        form_handlers: createPayload.form_handlers,
        configuration: {
          tools: agentForm.tools,
          knowledgeBase: agentForm.knowledgeBase
        }
      } : createPayload;

      const capturedAgentName = agentForm.name;

      // Save configuration updates directly to backend instead of dynamic per-agent stripe checkout
      const response = await fetch(url, {
        method: method,
        headers: getAuthHeaders(),
        body: JSON.stringify(agentPayload)
      });
      const resData = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(resData.message || resData.error || `Failed to ${isEdit ? 'update' : 'create'} agent configuration.`);
      }

      // Automatically trigger sync for any configured KB source if this is a newly created agent
      const newAgentId = resData.id;
      if (!isEdit && newAgentId && kbResources.length) {
        pollIngestionStatus(newAgentId);
      }
      if (!isEdit && newAgentId && kbResources.length) {
        const backendStartedSources = new Set(
          (resData.automatic_ingestion?.started || []).map(item => `${item.source_type}::${item.source_value}`)
        );
        for (const kb of kbResources) {
          if (backendStartedSources.has(`${kb.type}::${kb.value.trim()}`)) {
            continue;
          }
          const isOneDrive = kb.type === "onedrive";
          const isSharePoint = kb.type === "sharepoint";
          const isMicrosoftSource = isOneDrive || isSharePoint;
          let token = microsoftTokenForRequest;
          if (isMicrosoftSource) {
            try {
              token = await getFreshMicrosoftToken();
            } catch (tErr) {
              console.error("Failed to get fresh token", tErr);
            }
          }
          try {
            const endpoint = isSharePoint ? URL.INGEST_SHAREPOINT : isOneDrive ? URL.INGEST_ONEDRIVE : URL.INGEST_URL;
            const syncResponse = await fetch(endpoint, {
              method: "POST",
              headers: getAuthHeaders(),
              body: JSON.stringify(isMicrosoftSource ? {
                agent_id: newAgentId,
                folder_link: kb.value.trim(),
                access_token: token,
              } : {
                agent_id: newAgentId,
                url: kb.value.trim(),
                crawl: webCrawlPayload(kb),
              }),
            });
            if (!syncResponse.ok) {
              const syncData = await syncResponse.json().catch(() => ({}));
              throw new Error(syncData.message || syncData.error || "Failed to start automatic knowledge base sync");
            }
          } catch (syncErr) {
            console.error("Auto sync failed for source:", kb.value, syncErr);
            showMessage(syncErr.message || "Automatic knowledge base sync failed to start.", "error");
          }
        }
      }

      await loadCustomAgents();
      showMessage(`Agent '${capturedAgentName}' has been successfully ${isEdit ? 'updated' : 'created'}!`, "success");
      if (!isEdit) {
        localStorage.removeItem("agent_creation_draft");
      }
      resetAgentForm();
      setShowKnowledgebasePopup(false);

    } catch (error) {
      console.error("Error saving agent:", error);
      showMessage(error.message || "Error saving agent", "error");
    } finally {
      setLoading(false);
    }
  };


  const handleDeleteAgent = async (id) => {
    const confirmed = await showConfirm(
      "This agent and its saved configuration will be permanently removed.",
      {
        title: "Delete agent?",
        confirmLabel: "Delete agent",
        variant: "danger",
      }
    );
    if (!confirmed) return;
    setLoading(true);
    try {
      const response = await fetch(`${URL.CUSTOM_AGENTS}/${id}`, {
        method: "DELETE",
        headers: getAuthHeaders(),
      });
      if (!response.ok) throw new Error("Failed to delete agent");
      showMessage("Agent deleted successfully", "success");
      if (agentForm.id === id) {
        resetAgentForm();
        setShowKnowledgebasePopup(false);
      }
      localStorage.removeItem("agent_creation_draft");
      await loadCustomAgents();
    } catch (error) {
      console.error("Error deleting agent:", error);
      showMessage("Error deleting agent", "error");
    } finally {
      setLoading(false);
    }
  };




  const normalizeAgentToolsForEdit = (configuration = {}) => {
    const tools = [];
    const rawTools = Array.isArray(configuration.tools) ? configuration.tools : [];
    const hasApi = rawTools.some(isRestApiToolConfig) || !!configuration.api;
    const hasRawMcpTool = rawTools.some(tool => typeof tool === "object" && isMcpToolConfig(tool));
    const hasRawDatabaseTool = rawTools.some(tool => typeof tool === "object" && isDatabaseToolConfig(tool));
    const mcpConfig = configuration.mcp || configuration.mcp_config || configuration.mcpConfig || {};
    const dbConfig = (
      configuration.database ||
      configuration.database_config ||
      configuration.databaseConfig ||
      configuration.structured_database ||
      configuration.structuredDatabase ||
      {}
    );
    const apiEndpoints = configuration.api?.api_endpoints || configuration.api?.apiEndpoints || [];
    const mcpServers = configuration.api?.mcp_servers || configuration.api?.mcpServers || [];

    if (hasApi && Array.isArray(apiEndpoints) && apiEndpoints.length) {
      const endpoints = apiEndpoints;
      endpoints.forEach(endpoint => {
        const apiConfig = {
          kind: "rest",
          url: endpoint.url || "",
          method: endpoint.method || configuration.api?.req_type || configuration.api?.reqType || "GET",
          format: endpoint.format || "JSON",
          parameters: endpoint.parameters || [],
          routingProfile: normalizeRoutingProfile(endpoint.routing_profile || endpoint.routingProfile),
          testPrompt: ""
        };
        tools.push({
          type: "api",
          config: {
            ...apiConfig,
            verified: Boolean(apiConfig.url),
            verifiedSignature: Boolean(apiConfig.url) ? apiToolSignature(apiConfig) : "",
            verificationMessage: Boolean(apiConfig.url) ? "Previously saved endpoint." : "",
            verificationResult: null
          }
        });
      });
    }

    const mcpServerList = (
      mcpConfig.servers ||
      mcpConfig.mcp_servers ||
      mcpConfig.mcpServers ||
      mcpConfig.server ||
      mcpServers ||
      []
    );
    const normalizedMcpServers = Array.isArray(mcpServerList) ? mcpServerList : (mcpServerList ? [mcpServerList] : []);
    const hasMcp = rawTools.some(isMcpToolConfig) || Boolean(Object.keys(mcpConfig || {}).length);
    if (hasMcp && normalizedMcpServers.length) {
      normalizedMcpServers.forEach(server => {
        const mcpAuth = normalizeMcpAuthConfig(server);
        tools.push({
          type: "api",
          config: {
            kind: "mcp",
            name: server.name || "",
            url: server.url || server.endpoint || "",
            authType: mcpAuth.authType,
            authToken: mcpAuth.authToken,
            authHeaderName: mcpAuth.authHeaderName,
            allowedTools: server.allowedTools || server.allowed_tools || [],
            mcpTools: server.mcpTools || server.mcp_tools || [],
            routingProfile: normalizeRoutingProfile(server.routing_profile || server.routingProfile),
            testPrompt: "",
            ...resetApiVerification
          }
        });
      });
    } else if (hasMcp && !hasRawMcpTool) {
      tools.push({
        type: "api",
        config: {
          kind: "mcp",
          name: "",
          url: "",
          authType: "none",
          authToken: "",
          authHeaderName: "",
          allowedTools: [],
          mcpTools: [],
          testPrompt: "",
          ...resetApiVerification
        }
      });
    }

    const dbProfileList = dbConfig.profiles || dbConfig.connections || dbConfig.databases || dbConfig.profile || [];
    const normalizedDbProfiles = Array.isArray(dbProfileList) ? dbProfileList : (dbProfileList ? [dbProfileList] : []);
    const hasDatabase = rawTools.some(isDatabaseToolConfig) || Boolean(Object.keys(dbConfig || {}).length);
    if (hasDatabase && normalizedDbProfiles.length) {
      normalizedDbProfiles.slice(0, MAX_DATABASE_PROFILES_PER_AGENT).forEach(profile => {
        const rawConnectionUri = profile.connectionUri || profile.connection_uri || profile.databaseUrl || profile.database_url || "";
        const rawPassword = profile.password || profile.passwordSecret || profile.password_secret || "";
        const connectionUriIsSecret = String(rawConnectionUri || "").startsWith("fernet:");
        const passwordIsSecret = String(rawPassword || "").startsWith("fernet:");
        const selectedSchemas = profile.allowedSchemas || profile.allowed_schemas || profile.selectedSchemas || profile.schemas || [];
        const selectedTables = profile.allowedTables || profile.allowed_tables || profile.selectedTables || profile.tables || [];
        const selectedColumns = profile.allowedColumns || profile.allowed_columns || profile.selectedColumns || {};
        const detectedSchemas = profile.schemaMetadata || profile.schema_metadata || profile.detectedSchemas || [];
        const limits = profile.limits || {};
        const dbToolConfig = {
          kind: "database",
          engine: profile.engine || profile.type || "postgres",
          name: profile.name || "",
          purpose: profile.purpose || profile.description || profile.profilePurpose || profile.profile_purpose || "",
          routingProfile: normalizeRoutingProfile(profile.routing_profile || profile.routingProfile),
          host: profile.host || "",
          port: String(profile.port || getDatabaseEngineOption(profile.engine || profile.type || "postgres").defaultPort || ""),
          database: profile.database || profile.dbname || "",
          filePath: profile.filePath || profile.file_path || "",
          serviceName: profile.serviceName || profile.service_name || "",
          username: profile.username || profile.user || "",
          password: passwordIsSecret ? "" : rawPassword,
          passwordSecret: passwordIsSecret ? rawPassword : "",
          sslmode: profile.sslmode || profile.sslMode || "prefer",
          driver: profile.driver || "",
          dsn: profile.dsn || "",
          connectionUri: connectionUriIsSecret ? "" : rawConnectionUri,
          connectionUriSecret: connectionUriIsSecret ? rawConnectionUri : "",
          useConnectionUri: Boolean(rawConnectionUri),
          permissionMode: profile.permissionMode || profile.permission_mode || profile.mode || "read_only",
          rolePermissionsDetected: Boolean(profile.rolePermissionsDetected || profile.role_permissions_detected),
          accessModeOperations: profile.accessModeOperations || profile.access_mode_operations || [],
          roleAllowedOperations: profile.roleAllowedOperations || profile.role_allowed_operations || null,
          allowedOperations: profile.allowedOperations || profile.allowed_operations || [],
          selectedSchemas: Array.isArray(selectedSchemas) ? selectedSchemas : String(selectedSchemas || "").split(",").map(item => item.trim()).filter(Boolean),
          selectedTables: Array.isArray(selectedTables) ? selectedTables : String(selectedTables || "").split(",").map(item => item.trim()).filter(Boolean),
          selectedColumns: selectedColumns && typeof selectedColumns === "object" && !Array.isArray(selectedColumns) ? selectedColumns : {},
          detectedSchemas: Array.isArray(detectedSchemas) ? detectedSchemas : [],
          limits: {
            maxRows: limits.maxRows || limits.max_rows || 100,
            timeoutSeconds: limits.timeoutSeconds || limits.timeout_seconds || 15,
            maxResultBytes: limits.maxResultBytes || limits.max_result_bytes || 12000,
            maxAffectedRows: limits.maxAffectedRows || limits.max_affected_rows || 10
          }
        };
        tools.push({
          type: "database",
          config: {
            ...dbToolConfig,
            verified: Boolean(dbToolConfig.selectedSchemas.length),
            verifiedSignature: Boolean(dbToolConfig.selectedSchemas.length) ? databaseToolSignature(dbToolConfig) : "",
            verificationMessage: Boolean(dbToolConfig.selectedSchemas.length) ? "Previously saved database profile." : "",
            verificationResult: null
          }
        });
      });
    } else if (hasDatabase && !hasRawDatabaseTool) {
      tools.push({
        type: "database",
        config: createDefaultDatabaseToolConfig()
      });
    }

    (configuration.form_handlers || configuration.formHandlers || []).forEach(form => {
      tools.push({
        type: "form",
        config: {
          fields: form.fields || [],
          submitAction: getFormSubmitActions(form)[0] || "email",
          submitActions: getFormSubmitActions(form),
          apiEndpoint: form.apiEndpoint || form.api_endpoint || "",
          triggerDescription: form.triggerDescription || "",
          triggerKeywords: Array.isArray(form.triggerKeywords) ? form.triggerKeywords.join(", ") : form.triggerKeywords || "",
          routingProfile: normalizeRoutingProfile(form.routing_profile || form.routingProfile),
          confirmationMessage: form.confirmationMessage || "Please complete the form below."
        }
      });
    });

    rawTools.forEach(tool => {
      if (tool && typeof tool === "object" && tool.type && tool.config && !tools.some(existing => existing === tool)) {
        tools.push(tool);
      }
    });

    return tools;
  };

  const normalizeKnowledgeBaseForEdit = (configuration = {}) => {
    const existing = configuration.knowledgeBase;
    if (Array.isArray(existing) && existing.length) return existing;

    const kb = configuration.knowledge_base || configuration.knowledgeBase || {};
    const rows = [];
    const webSources = kb.web_sources || kb.webSources || [];
    const sourceRows = Array.isArray(webSources) ? webSources : (webSources ? [webSources] : []);
    const seenWebUrls = new Set();
    sourceRows.forEach(source => {
      if (typeof source === "string") {
        const url = source.trim();
        if (url) {
          rows.push({ type: "website", value: url, crawl: { ...DEFAULT_WEB_CRAWL_SETTINGS } });
          seenWebUrls.add(url);
        }
        return;
      }
      if (source && typeof source === "object") {
        const url = String(source.url || source.web_url || source.webUrl || "").trim();
        if (url) {
          rows.push({ type: "website", value: url, crawl: normalizeWebCrawlSettings(source.crawl || source) });
          seenWebUrls.add(url);
        }
      }
    });
    const webUrls = kb.web_urls || kb.webUrls || kb.web_url || kb.webUrl;
    const urls = Array.isArray(webUrls) ? webUrls : (webUrls ? [webUrls] : []);
    urls.filter(Boolean).forEach(url => {
      const value = String(url).trim();
      if (value && !seenWebUrls.has(value)) {
        rows.push({ type: "website", value, crawl: { ...DEFAULT_WEB_CRAWL_SETTINGS } });
      }
    });

    const oneDriveFolders = kb.onedrive_folder_ids || kb.onedriveFolderIds || kb.onedrive_folder_id || kb.onedriveFolderId;
    const folders = Array.isArray(oneDriveFolders) ? oneDriveFolders : (oneDriveFolders ? [oneDriveFolders] : []);
    folders.filter(Boolean).forEach(folder => rows.push({ type: "onedrive", value: folder }));

    const sharePointFolders = kb.sharepoint_folder_ids || kb.sharepointFolderIds || kb.sharepoint_folder_id || kb.sharepointFolderId;
    const spFolders = Array.isArray(sharePointFolders) ? sharePointFolders : (sharePointFolders ? [sharePointFolders] : []);
    spFolders.filter(Boolean).forEach(folder => rows.push({ type: "sharepoint", value: folder }));

    return rows.length ? rows : [{ type: 'onedrive', value: '' }];
  };

  const handleEditAgent = (agent) => {
    const configuration = agent.configuration || {};
    const normalizedTools = normalizeAgentToolsForEdit(configuration);
    const normalizedKb = normalizeKnowledgeBaseForEdit(configuration);
    setAgentForm({
      id: agent.id,
      name: agent.name,
      description: agent.description || "",
      tools: normalizedTools,
      knowledgeBase: normalizedKb,
      kbRoutingProfile: normalizeRoutingProfile(
        configuration.knowledge_base?.routing_profile
        || configuration.knowledge_base?.routingProfile
        || configuration.knowledgeBase?.routing_profile
        || configuration.knowledgeBase?.routingProfile
      ),
      microsoft_token: agent.microsoft_token || "",
      whatsappEnabled: Boolean(
        configuration.whatsapp_enabled ??
        configuration.whatsappEnabled ??
        agent.whatsapp_enabled
      ),
    });
    setHasApiSelected(normalizedTools.some(isRestApiToolConfig));
    setHasMcpSelected(normalizedTools.some(isMcpToolConfig));
    setHasDatabaseSelected(normalizedTools.some(isDatabaseToolConfig));
    setHasFormSelected(normalizedTools.some(t => t.type === 'form'));
    setHasKbSelected(normalizedKb.some(kb => (kb.value || '').trim()));
    setShowKnowledgebasePopup(true);
  };

  const handleBackToPreLogin = () => {
    navigate("/");
  };

  const getRoleDisplayName = () => {
    switch (userRole) {
      case "admin": return "Administrator";
      case "member": return "Member";
      default: return "User";
    }
  };

  const getWelcomeText = () => {
    const firstName = userInfo.firstName;
    const lastName = userInfo.lastName;

    if (firstName && lastName) {
      return `Welcome, ${firstName} ${lastName}`;
    } else if (firstName) {
      return `Welcome, ${firstName}`;
    } else if (lastName) {
      return `Welcome, ${lastName}`;
    } else {
      return `Welcome, ${getRoleDisplayName()}`;
    }
  };

  const getWorkspaceText = () => {
    if (company?.name) return `Workspace: ${company.name}`;
    if (company?.domain) return `Workspace: ${company.domain}`;
    return "Workspace: (unknown)";
  };

  const databaseProfileCount = agentForm.tools.filter(isDatabaseToolConfig).length;

  return (
    <div className="dashboard-page">
      <Navbar currentPage="dashboard" />

      <div className="container dashboard-container">
        {/* Header with user info and action buttons */}
        <div className="dashboard-header">
          <div className="dashboard-title">
            <BackArrow
              onClick={handleBackToPreLogin}
              title="Back to pre-login"
              style={{ marginBottom: "10px" }}
            />
            <h1 className="dashboard-main-title">Agent Dashboard</h1>
            <div className="dashboard-meta-row">
              <div className="dashboard-meta-box">
                {getWelcomeText()}
              </div>
              <div className="dashboard-meta-box">
                {getWorkspaceText()}
              </div>
              <div className="dashboard-meta-box" style={{ background: 'rgba(146, 0, 220, 0.15)', borderColor: 'rgba(146, 0, 220, 0.45)', color: '#d8b4fe' }}>
                Plan: {(company?.subscription_tier || "free").toUpperCase()}
              </div>
            </div>
          </div>

          <div className="dashboard-actions">
            {/* Admin Dashboard Button - Only show for admin users */}
            {userRole === "admin" && (
              <button
                className="admin-dashboard-button"
                onClick={handleAdminDashboard}
                style={{
                  background: 'linear-gradient(135deg, #6c5ce7, #a29bfe)',
                  color: 'white',
                  padding: '10px 20px',
                  fontSize: '14px',
                  fontWeight: '600',
                  border: 'none',
                  borderRadius: '25px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '8px',
                  transition: 'all 0.3s ease',
                  boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
                  whiteSpace: 'nowrap'
                }}
                onMouseOver={(e) => {
                  e.target.style.transform = 'translateY(-2px)';
                  e.target.style.background = 'linear-gradient(135deg, #5f3dc4, #9775fa)';
                  e.target.style.boxShadow = '0 6px 18px rgba(0, 0, 0, 0.4)';
                }}
                onMouseOut={(e) => {
                  e.target.style.transform = 'translateY(0)';
                  e.target.style.background = 'linear-gradient(135deg, #6c5ce7, #a29bfe)';
                  e.target.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.3)';
                }}
              >
                <FaUserCog style={{ fontSize: '16px' }} />
                Admin Dashboard
              </button>
            )}

            <LogoutButton />

          </div>
        </div>

        {/* Agents Grid and Management */}
        <div className="workspace-content">
          {message.text && (
            <div className={`message-banner ${message.type}`} style={{
              position: 'fixed',
              top: '84px',
              left: '50%',
              transform: 'translateX(-50%)',
              width: 'min(92vw, 680px)',
              zIndex: 2000,
              padding: '12px 20px',
              borderRadius: '8px',
              marginBottom: '20px',
              textAlign: 'center',
              background: message.type === 'success' ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)',
              color: message.type === 'success' ? '#4ade80' : '#f87171',
              border: `1px solid ${message.type === 'success' ? '#4ade80' : '#f87171'}`
            }}>
              {message.text}
            </div>
          )}

          {company?.subscription_status === 'trialing' && getRemainingTrialDays() > 0 && (
            <div style={{
              background: 'linear-gradient(135deg, rgba(146, 0, 220, 0.15), rgba(108, 92, 231, 0.15))',
              border: '1px solid rgba(146, 0, 220, 0.45)',
              borderRadius: '16px',
              padding: '16px 24px',
              marginBottom: '30px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              boxShadow: '0 8px 32px rgba(146, 0, 220, 0.1)'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                <span style={{ fontSize: '24px' }}>⚡</span>
                <div style={{ textAlign: 'left' }}>
                  <h4 style={{ color: '#fff', margin: '0 0 4px', fontSize: '15px', fontWeight: '700' }}>
                    Business Trial Active
                  </h4>
                  <p style={{ color: 'rgba(255, 255, 255, 0.7)', margin: 0, fontSize: '13px' }}>
                    You have <strong style={{ color: '#d8b4fe' }}>{getRemainingTrialDays()} days</strong> remaining in your free trial of the Business Plan.
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowBillingPopup(true)}
                style={{
                  background: '#9200dc',
                  color: '#fff',
                  border: 'none',
                  padding: '10px 20px',
                  borderRadius: '10px',
                  fontSize: '13px',
                  fontWeight: '600',
                  cursor: 'pointer',
                  boxShadow: '0 4px 12px rgba(146, 0, 220, 0.3)',
                  transition: 'all 0.3s'
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = '#8000c4'}
                onMouseLeave={(e) => e.currentTarget.style.background = '#9200dc'}
              >
                Upgrade to Keep Features
              </button>
            </div>
          )}

          {userRole === "admin" && !isSuperAdmin && (
            <div className="workspace-controls">
              <button
                className="add-agent-main-btn"
                onClick={openManageAgentsForm}
                style={{
                  background: 'rgba(146, 0, 220, 0.2)',
                  border: '1px solid #9200dc',
                  color: '#fff',
                  padding: '12px 24px',
                  borderRadius: '12px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '10px',
                  fontWeight: '600',
                  transition: 'all 0.3s'
                }}
              >
                <FaPlus /> Create new agent
              </button>
              <button
                type="button"
                className="whatsapp-config-trigger"
                onClick={() => setShowWhatsappConfiguration(true)}
              >
                <FaWhatsapp aria-hidden="true" />
                Configure WhatsApp
              </button>
            </div>
          )}

          {customAgents.length > 0 ? (
            <div className="agents-grid-container">
              <div className="custom-agents-section" style={{ marginBottom: "40px" }}>
                <h2
                  style={{
                    color: "#fff",
                    fontSize: "20px",
                    marginBottom: "20px",
                    display: "flex",
                    alignItems: "center",
                    gap: "10px",
                  }}
                >
                  <FaRobot color="#9200dc" /> Custom Agents
                </h2>
                <div className="agents-grid dashboard-agents-grid">
                  {customAgents.map((agent) => {
                    const expired = isAgentExpired(agent);
                    return (
                      <div key={agent.id} className="agent-card-shell">
                        <AgentCard
                          agentName={agent.name}
                          iconSrc="/bot.png"
                          description={agent.description || ""}
                          toolLabels={getConfiguredToolLabels(agent)}
                          showToolsToggle={userRole === "admin"}
                          isExpired={expired}
                          onClick={() => {
                            if (expired) {
                              if (userRole === "admin") {
                                showMessage("This agent's subscription has expired. Please process checkout to reactivate it.", "info");
                                handleEditAgent(agent);
                              } else {
                                showMessage("This agent's subscription has expired. Please contact your administrator to reactivate it.", "error");
                              }
                            } else {
                              setAgentModeChoice(agent);
                            }
                          }}
                        />
                      {userRole === "admin" && (
                        <div
                          className="agent-card-actions"
                          style={{
                            position: "absolute",
                            top: "10px",
                            right: "16px",
                            display: "flex",
                            gap: "8px",
                            zIndex: 10,
                          }}
                        >
                          <button
                            onClick={(event) => {
                              event.stopPropagation();
                              handleEditAgent(agent);
                            }}
                            title="Edit Agent"
                            style={{
                              background: "transparent",
                              border: "none",
                              color: "#fff",
                              padding: "4px",
                              borderRadius: 0,
                              cursor: "pointer",
                              fontSize: "16px",
                            }}
                          >
                            <FaEdit />
                          </button>
                          <button
                            onClick={(event) => {
                              event.stopPropagation();
                              handleDeleteAgent(agent.id);
                            }}
                            title="Delete Agent"
                            style={{
                              background: "transparent",
                              border: "none",
                              color: "#f87171",
                              padding: "4px",
                              borderRadius: 0,
                              cursor: "pointer",
                              fontSize: "16px",
                            }}
                          >
                            <FaTrash />
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                </div>
              </div>
            </div>
          ) : (
            <div
              className="empty-workspace-state"
              style={{
                textAlign: "center",
                padding: "80px 40px",
                background: "rgba(255, 255, 255, 0.03)",
                border: "1px dashed rgba(255, 255, 255, 0.1)",
                borderRadius: "24px",
                color: "#fff",
                marginTop: "20px",
              }}
            >
              <div
                style={{
                  fontSize: "64px",
                  color: "#9200dc",
                  marginBottom: "30px",
                  opacity: 0.8,
                }}
              >
                <FaRobot />
              </div>
              <h2 style={{ fontSize: "28px", marginBottom: "15px" }}>
                Create New Agents to Begin
              </h2>
              <p style={{ color: "#aaa", maxWidth: "500px", margin: "0 auto 40px" }}>
                Your workspace is currently empty. As an administrator, you can
                create custom agents tailored to your business needs by clicking the
                button below.
              </p>
              {userRole === "admin" && !isSuperAdmin ? (
                <button
                  className="create-first-agent-btn"
                  onClick={toggleKnowledgebasePopup}
                  style={{
                    background: "#9200dc",
                    color: "#fff",
                    padding: "16px 32px",
                    borderRadius: "12px",
                    fontSize: "16px",
                    fontWeight: "600",
                    border: "none",
                    cursor: "pointer",
                    boxShadow: "0 8px 16px rgba(146, 0, 220, 0.3)",
                    transition: "all 0.3s",
                  }}
                >
                  <FaPlus style={{ marginRight: "10px" }} /> Create Your First Agent
                </button>
              ) : (
                <p style={{ color: "#ccc" }}>
                  Waiting for administrator to set up agents...
                </p>
              )}
            </div>
          )}
        </div>



        {/* User Info */}
        <div className="dashboard-role-summary" style={{
          marginTop: '60px',
          padding: '20px',
          background: 'rgba(255, 255, 255, 0.05)',
          borderRadius: '12px',
          textAlign: 'center'
        }}>
          <p style={{ color: '#ccc', fontSize: '14px', margin: 0 }}>
            Your Role: <strong>{getRoleDisplayName()}</strong> |
            Access Level: <strong>{customAgents.length + accessibleAgents.length} Agent{(customAgents.length + accessibleAgents.length) !== 1 ? 's' : ''}</strong>
            {userInfo.email && (
              <>
                {' | '}
                Email: <strong>{userInfo.email}</strong>
              </>
            )}
          </p>
        </div>
      </div>

      {/* Chat Agent vs Voice Agent selection */}
      {agentModeChoice && (
        <div
          className="popup-overlay-management"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            background: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            zIndex: 1000,
            padding: '20px',
          }}
          onClick={() => setAgentModeChoice(null)}
        >
          <div
            style={{
              background: '#0a0a0c',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              borderRadius: '20px',
              width: '100%',
              maxWidth: '420px',
              padding: '32px',
              position: 'relative',
              boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
              textAlign: 'center',
            }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              onClick={() => setAgentModeChoice(null)}
              style={{
                position: 'absolute',
                top: '16px',
                right: '18px',
                background: 'none',
                border: 'none',
                color: '#fff',
                fontSize: '20px',
                cursor: 'pointer',
              }}
            >
              ✕
            </button>

            <h2 style={{ color: '#fff', margin: '0 0 8px', fontSize: '20px' }}>{agentModeChoice.name}</h2>
            <p style={{ color: '#aaa', margin: '0 0 28px', fontSize: '13px' }}>
              How would you like to talk to this agent?
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <button
                onClick={() => {
                  const agentId = agentModeChoice.id;
                  setAgentModeChoice(null);
                  handleAgentClick(`/chat/${agentId}`);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '10px',
                  padding: '14px 20px',
                  borderRadius: '12px',
                  border: '1px solid rgba(146, 0, 220, 0.5)',
                  background: 'rgba(146, 0, 220, 0.15)',
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: 'pointer',
                }}
              >
                💬 Chat Agent
              </button>
              <button
                onClick={() => {
                  const agentId = agentModeChoice.id;
                  setAgentModeChoice(null);
                  handleAgentClick(`/voice-chat/${agentId}`);
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '10px',
                  padding: '14px 20px',
                  borderRadius: '12px',
                  border: '1px solid rgba(0, 166, 81, 0.5)',
                  background: 'rgba(0, 166, 81, 0.15)',
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: '14px',
                  cursor: 'pointer',
                }}
              >
                🎙️ Voice Agent
              </button>
            </div>
          </div>
        </div>
      )}

      <WhatsAppConfiguration
        open={showWhatsappConfiguration}
        onClose={() => setShowWhatsappConfiguration(false)}
        entitlement={company?.features?.whatsapp}
        getAuthHeaders={getAuthHeaders}
        onConnectionChange={setWhatsappConnection}
      />

      {/* Agent Creation Modal (formerly in Admin Dashboard) */}
      {showKnowledgebasePopup && (
        <div className="popup-overlay-management custom-agent-modal" style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          background: 'rgba(0, 0, 0, 0.2)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 1000,
          padding: '20px',
          overflow: 'hidden'
        }}>
          <div className="popup-content-management" style={{
            background: '#0a0a0c',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: '24px',
            width: '100%',
            maxWidth: '1150px',
            maxHeight: '90vh',
            overflowY: 'auto',
            position: 'relative',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)'
          }}>
            <button className="popup-close-button" onClick={toggleKnowledgebasePopup} style={{
              position: 'absolute',
              top: '20px',
              right: '25px',
              background: 'none',
              border: 'none',
              color: '#fff',
              fontSize: '24px',
              cursor: 'pointer',
              zIndex: 1001
            }}>✕</button>

            <div className="agent-creation-header" style={{ padding: '40px 40px 0' }}>
              <h2 style={{ color: '#fff', margin: 0, display: 'flex', alignItems: 'center', gap: '15px' }}>
                <FaDatabase style={{ color: '#9200dc' }} /> {agentForm.id ? 'Edit Agent' : 'Manage Knowledgebase'}
              </h2>
              <p style={{ color: '#aaa', margin: '10px 0 0' }}>Configure your agents, tools, and knowledge sources.</p>
            </div>

            <div className="agent-creation-container" style={{ padding: '40px' }}>
              {/* Agent Details Section */}
              <div className="agent-form-section-custom" style={{ marginBottom: '40px' }}>
                <h3 style={{ color: '#b026ff', fontSize: '14px', textTransform: 'uppercase', letterSpacing: '2px', marginBottom: '25px' }}>SECTION 1: Agent Details</h3>
                <div className="form-group" style={{ marginBottom: '20px' }}>
                  <label style={{ color: '#fff', display: 'block', marginBottom: '10px' }}>Agent Name *</label>
                  <input
                    type="text"
                    placeholder="Enter agent name (e.g. Sales Assistant)"
                    value={agentForm.name}
                    onChange={(e) => {
                      setAgentForm({ ...agentForm, name: e.target.value });
                      if (validationErrors.name) {
                        setValidationErrors(prev => {
                          const copy = { ...prev };
                          delete copy.name;
                          return copy;
                        });
                      }
                    }}
                    className="admin-input-custom"
                    style={{
                      border: validationErrors.name ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)'
                    }}
                  />
                  {validationErrors.name && (
                    <span style={{ color: '#ef4444', fontSize: '11px', marginTop: '6px', display: 'block' }}>
                      {validationErrors.name}
                    </span>
                  )}
                </div>
                <div className="form-group">
                  <label style={{ color: '#fff', display: 'block', marginBottom: '10px' }}>Agent Description</label>
                  <textarea
                    placeholder="Briefly describe what this agent does (e.g. Handles employee leave requests)"
                    value={agentForm.description}
                    onChange={(e) => setAgentForm({ ...agentForm, description: e.target.value })}
                    className="admin-input-custom"
                    rows={3}
                    style={{ resize: 'vertical', minHeight: '80px' }}
                  />
                </div>
                <AgentWhatsAppAccessOption
                  visible={whatsappAvailable || Boolean(agentForm.whatsappEnabled)}
                  enabled={Boolean(agentForm.whatsappEnabled)}
                  available={whatsappAvailable}
                  phoneNumber={whatsappConnection.phone_number}
                  onChange={(enabled) => {
                    setAgentForm((current) => ({
                      ...current,
                      whatsappEnabled: enabled,
                    }));
                  }}
                />
              </div>
              {/* Tool Selection Section */}
              <div className="agent-form-section-custom" style={{ marginBottom: '40px' }}>
                <h3 style={{ color: '#b026ff', fontSize: '14px', textTransform: 'uppercase', letterSpacing: '2px', marginBottom: '6px' }}>SECTION 2: Plan &amp; Tool Selection</h3>
                <p style={{ color: '#888', fontSize: '13px', marginBottom: '24px', marginTop: 0 }}>Your active plan determines which tools you can enable for this agent.</p>

                {/* Package selection cards */}
                <div className="pkg-selection-grid">
                  {(() => {
                    const getFeaturesForPackage = (pkg) => {
                      if (!pkg) return [];
                      const lines = [];
                      const allowed = buildPackageFeatures(pkg);
                      const locked = buildPackageLimitations(pkg);
                      
                      const showTerms = ["Web URL", "OneDrive", "SharePoint", "Form Builder", "APIs", "MCP"];
                      
                      allowed.forEach(feat => {
                        const term = showTerms.find(t => feat.includes(t));
                        if (term) {
                          lines.push({ label: feat, yes: true });
                        }
                      });
                      
                      locked.forEach(lim => {
                        const term = showTerms.find(t => lim.includes(t));
                        if (term) {
                          const label = lim.replace(/^No\s+/, "");
                          lines.push({ label: label, yes: false });
                        }
                      });
                      
                      if (pkg.id === 'enterprise') {
                        lines.push({ label: 'Unlimited agents', yes: true });
                      }
                      
                      return lines;
                    };

                    const pkgOrder = ['free', 'starter', 'business', 'enterprise'];
                    const displayPackages = pkgOrder.map(id => {
                      const pkg = subscriptionPackages.find(p => String(p.id).toLowerCase() === id);
                      if (!pkg) {
                        if (id === 'free') return { id: 'free', name: 'Free Trial', price: 'Default tier', features: [ { label: 'Web URL Knowledge Base', yes: true }, { label: 'OneDrive Knowledge Base', yes: false }, { label: 'SharePoint Knowledge Base', yes: false }, { label: 'Form Builder', yes: false }, { label: 'APIs', yes: false }, { label: 'MCP', yes: false } ] };
                        if (id === 'starter') return { id: 'starter', name: 'Starter', price: 'Basic tier', features: [ { label: 'Web URL Knowledge Base', yes: true }, { label: 'OneDrive Knowledge Base', yes: true }, { label: 'SharePoint Knowledge Base', yes: true }, { label: 'Form Builder', yes: true }, { label: 'APIs', yes: false }, { label: 'MCP', yes: false } ] };
                        if (id === 'business') return { id: 'business', name: 'Business', price: 'Mid tier', features: [ { label: 'Web URL Knowledge Base', yes: true }, { label: 'OneDrive Knowledge Base', yes: true }, { label: 'SharePoint Knowledge Base', yes: true }, { label: 'Form Builder', yes: true }, { label: 'APIs', yes: false }, { label: 'MCP', yes: false } ] };
                        return { id: 'enterprise', name: 'Enterprise', price: 'Full access', features: [ { label: 'Web URL Knowledge Base', yes: true }, { label: 'OneDrive Knowledge Base', yes: true }, { label: 'SharePoint Knowledge Base', yes: true }, { label: 'Form Builder', yes: true }, { label: 'APIs', yes: true }, { label: 'MCP', yes: true }, { label: 'Unlimited agents', yes: true } ] };
                      }
                      return {
                        id: pkg.id.toLowerCase(),
                        name: pkg.name || pkg.id,
                        price: pkg.description || (pkg.price_monthly_usd != null ? `$${pkg.price_monthly_usd}/mo` : 'Basic tier'),
                        features: getFeaturesForPackage(pkg)
                      };
                    });

                    return displayPackages;
                  })().map(pkg => {
                    const isCurrent = activeTier === pkg.id;
                    const pkgOrder = ['free', 'starter', 'business', 'enterprise'];
                    const currentIdx = pkgOrder.indexOf(activeTier);
                    const pkgIdx = pkgOrder.indexOf(pkg.id);
                    const isLocked = pkgIdx > currentIdx;
                    return (
                      <div
                        key={pkg.id}
                        className={[
                          'pkg-card',
                          isCurrent ? 'pkg-card--current pkg-card--active' : '',
                          isLocked ? 'pkg-card--locked' : '',
                        ].filter(Boolean).join(' ')}
                        title={isLocked ? `Upgrade to ${pkg.name} to unlock these tools` : ''}
                      >
                        {isCurrent && (
                          <span className="pkg-card-badge pkg-card-badge--current">Your Plan</span>
                        )}
                        {isLocked && (
                          <span className="pkg-card-badge pkg-card-badge--upgrade">Upgrade</span>
                        )}
                        <div className="pkg-card-name">{pkg.name}</div>
                        <div className="pkg-card-price">{pkg.price}</div>
                        <ul className="pkg-card-features">
                          {pkg.features.map((f, i) => (
                            <li key={i} className="pkg-card-feature">
                              <span className={`feat-icon ${f.yes ? 'feat-icon--yes' : 'feat-icon--no'}`}>
                                {f.yes ? '✔' : '✖'}
                              </span>
                              {f.label}
                            </li>
                          ))}
                        </ul>
                        {isLocked && (
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setShowKnowledgebasePopup(false); setShowBillingPopup(true); }}
                            style={{ pointerEvents: 'auto', marginTop: '14px', width: '100%', background: 'rgba(108,92,231,0.25)', border: '1px solid #6c5ce7', color: '#a29bfe', borderRadius: '8px', padding: '7px', fontSize: '12px', fontWeight: '600', cursor: 'pointer' }}
                          >
                            Upgrade to {pkg.name}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
                
                <div style={{ marginTop: '8px' }}>
                  <p style={{ color: '#777', fontSize: '12px', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '14px', marginTop: 0 }}>Enable tools for this agent</p>

                  {/* Knowledge Base */}
                  <label className={`tool-checkbox-row ${hasKbSelected ? 'tool-checkbox-row--active' : ''} ${validationErrors.toolsSelection ? 'tool-row-error-blink' : ''}`}>
                    <input
                      type="checkbox"
                      checked={hasKbSelected}
                      onChange={e => handleCheckboxChange('kb', e.target.checked)}
                    />
                    <span className="tool-checkbox-custom" aria-hidden="true"></span>
                    <div className="tool-checkbox-label-text">
                      <strong>🗄️ Knowledge Base</strong>
                      <span>Scrape websites or connect OneDrive/SharePoint documents as the agent's knowledge source</span>
                    </div>
                    <span style={{ marginLeft: 'auto', color: '#4ade80', fontSize: '12px', fontWeight: 700, whiteSpace: 'nowrap' }}>Always included</span>
                  </label>

                  {/* API Endpoint */}
                  <label
                    className={`tool-checkbox-row ${hasApiSelected ? 'tool-checkbox-row--active' : ''} ${!allowsEndpointTools ? 'tool-checkbox-row--disabled' : ''}`}
                    title={!allowsEndpointTools ? 'Requires Enterprise plan' : ''}
                  >
                    <input
                      type="checkbox"
                      checked={hasApiSelected}
                      disabled={!allowsEndpointTools}
                      onChange={e => handleCheckboxChange('api', e.target.checked)}
                    />
                    <span className="tool-checkbox-custom" aria-hidden="true"></span>
                    <div className="tool-checkbox-label-text">
                      <strong>🔌 API Endpoint Tool {!allowsEndpointTools && <span style={{ color: '#a29bfe', fontSize: '11px', fontWeight: 'normal' }}>(Enterprise)</span>}</strong>
                      <span>Connect external REST APIs — agent extracts parameters from user messages</span>
                    </div>
                    {!allowsEndpointTools && (
                      <span style={{ marginLeft: 'auto', color: '#a29bfe', fontSize: '11px', fontWeight: 600, whiteSpace: 'nowrap' }}>Upgrade needed</span>
                    )}
                  </label>

                  {/* MCP Tool */}
                  <label
                    className={`tool-checkbox-row ${hasMcpSelected ? 'tool-checkbox-row--active' : ''} ${!allowsEndpointTools ? 'tool-checkbox-row--disabled' : ''}`}
                    title={!allowsEndpointTools ? 'Requires Enterprise plan' : ''}
                  >
                    <input
                      type="checkbox"
                      checked={hasMcpSelected}
                      disabled={!allowsEndpointTools}
                      onChange={e => handleCheckboxChange('mcp', e.target.checked)}
                    />
                    <span className="tool-checkbox-custom" aria-hidden="true"></span>
                    <div className="tool-checkbox-label-text">
                      <strong>MCP Tool {!allowsEndpointTools && <span style={{ color: '#a29bfe', fontSize: '11px', fontWeight: 'normal' }}>(Enterprise)</span>}</strong>
                      <span>Connect Model Context Protocol servers as a separate tool option</span>
                    </div>
                    {!allowsEndpointTools && (
                      <span style={{ marginLeft: 'auto', color: '#a29bfe', fontSize: '11px', fontWeight: 600, whiteSpace: 'nowrap' }}>Upgrade needed</span>
                    )}
                  </label>

                  {/* Database Tool */}
                  <label
                    className={`tool-checkbox-row ${hasDatabaseSelected ? 'tool-checkbox-row--active' : ''} ${!allowsEndpointTools ? 'tool-checkbox-row--disabled' : ''}`}
                    title={!allowsEndpointTools ? 'Requires Enterprise plan' : ''}
                  >
                    <input
                      type="checkbox"
                      checked={hasDatabaseSelected}
                      disabled={!allowsEndpointTools}
                      onChange={e => handleCheckboxChange('database', e.target.checked)}
                    />
                    <span className="tool-checkbox-custom" aria-hidden="true"></span>
                    <div className="tool-checkbox-label-text">
                      <strong><FaDatabase size={13} style={{ marginRight: '6px', verticalAlign: '-1px' }} />Database Tool {!allowsEndpointTools && <span style={{ color: '#a29bfe', fontSize: '11px', fontWeight: 'normal' }}>(Enterprise)</span>}</strong>
                      <span>Connect a read-only structured database role, test access, then select schemas the agent can query</span>
                    </div>
                    {!allowsEndpointTools && (
                      <span style={{ marginLeft: 'auto', color: '#a29bfe', fontSize: '11px', fontWeight: 600, whiteSpace: 'nowrap' }}>Upgrade needed</span>
                    )}
                  </label>

                  {/* Form Builder */}
                  <label
                    className={`tool-checkbox-row ${hasFormSelected ? 'tool-checkbox-row--active' : ''} ${!allowsFormBuilder ? 'tool-checkbox-row--disabled' : ''}`}
                    title={!allowsFormBuilder ? 'Requires Business or Enterprise plan' : ''}
                  >
                    <input
                      type="checkbox"
                      checked={hasFormSelected}
                      disabled={!allowsFormBuilder}
                      onChange={e => handleCheckboxChange('form', e.target.checked)}
                    />
                    <span className="tool-checkbox-custom" aria-hidden="true"></span>
                    <div className="tool-checkbox-label-text">
                      <strong>📋 Form Builder {!allowsFormBuilder && <span style={{ color: '#a29bfe', fontSize: '11px', fontWeight: 'normal' }}>(Business/Enterprise)</span>}</strong>
                      <span>Build AI-driven conversational forms with email or API submission</span>
                    </div>
                    {!allowsFormBuilder && (
                      <span style={{ marginLeft: 'auto', color: '#a29bfe', fontSize: '11px', fontWeight: 600, whiteSpace: 'nowrap' }}>Upgrade needed</span>
                    )}
                  </label>
                </div>
              </div>
              {/* Tool Configuration Cards (shown when tools are enabled) */}
              {agentForm.tools.length > 0 && (
                <div className="agent-form-section-custom" style={{ marginBottom: '40px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '20px' }}>
                    <h3 style={{ color: '#b026ff', fontSize: '14px', textTransform: 'uppercase', letterSpacing: '2px', margin: 0 }}>SECTION 3: Tool Configuration</h3>
                    {hasDatabaseSelected && (
                      <button
                        type="button"
                        onClick={addDatabaseTool}
                        disabled={databaseProfileCount >= MAX_DATABASE_PROFILES_PER_AGENT}
                        title={databaseProfileCount >= MAX_DATABASE_PROFILES_PER_AGENT ? `Maximum ${MAX_DATABASE_PROFILES_PER_AGENT} database profiles per agent` : 'Add another database profile'}
                        style={{
                          background: databaseProfileCount >= MAX_DATABASE_PROFILES_PER_AGENT ? 'rgba(255,255,255,0.06)' : 'rgba(146, 0, 220, 0.2)',
                          border: databaseProfileCount >= MAX_DATABASE_PROFILES_PER_AGENT ? '1px solid rgba(255,255,255,0.12)' : '1px solid #9200dc',
                          color: '#fff',
                          padding: '8px 12px',
                          borderRadius: '8px',
                          cursor: databaseProfileCount >= MAX_DATABASE_PROFILES_PER_AGENT ? 'not-allowed' : 'pointer',
                          fontSize: '12px',
                          fontWeight: 700,
                        }}
                      >
                        Add Database Profile ({databaseProfileCount}/{MAX_DATABASE_PROFILES_PER_AGENT})
                      </button>
                    )}
                  </div>
                  <div className="tools-list">
                    {agentForm.tools.map((tool, tIdx) => (
                    <div key={tIdx} className="tool-config-card" style={{ background: 'rgba(255,255,255,0.03)', padding: '20px', borderRadius: '12px', marginBottom: '20px', border: '1px solid rgba(255,255,255,0.05)' }}>
                      <div className="tool-card-header" style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '20px' }}>
                        <h4 style={{ color: '#fff', margin: 0 }}>{isMcpToolConfig(tool) ? 'MCP Server' : isDatabaseToolConfig(tool) ? 'Database Profile' : tool.type === 'api' ? 'API Endpoint' : 'Form Builder'}</h4>
                        <button className="remove-tool-btn" onClick={() => removeTool(tIdx)} style={{ background: 'none', border: 'none', color: '#f87171', cursor: 'pointer' }}>✕</button>
                      </div>

                      <RoutingProfileEditor
                        value={tool.config.routingProfile || tool.config.routing_profile}
                        onChange={(routingProfile) => updateToolConfig(tIdx, { routingProfile })}
                      />

                      {isMcpToolConfig(tool) ? (
                        <div className="mcp-tool-config">
                          <div style={{ display: 'grid', gap: '16px', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginBottom: '20px' }}>
                            <div>
                              <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>MCP Server Name</label>
                              <input
                                type="text"
                                style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                                placeholder="e.g. Company Tools"
                                value={tool.config.name || ''}
                                onChange={(e) => updateToolConfig(tIdx, { name: e.target.value })}
                              />
                            </div>
                            <div>
                              <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>MCP Server URL</label>
                              <input
                                type="text"
                                style={{
                                  width: '100%',
                                  background: 'rgba(0,0,0,0.2)',
                                  border: (validationErrors.mcp && !(tool.config.url || "").trim()) ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)',
                                  color: '#fff',
                                  padding: '10px',
                                  borderRadius: '8px'
                                }}
                                placeholder="e.g. https://mcp.company.com/mcp"
                                value={tool.config.url || ''}
                                onChange={(e) => updateToolConfig(tIdx, { url: e.target.value, mcpTools: [], allowedTools: [], ...resetApiVerification })}
                              />
                              {validationErrors.mcp && !(tool.config.url || "").trim() && (
                                <span style={{ color: '#ef4444', fontSize: '11px', marginTop: '6px', display: 'block' }}>
                                  {validationErrors.mcp}
                                </span>
                              )}
                            </div>
                            <div>
                              <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>Authentication</label>
                              <select
                                style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                                value={tool.config.authType || 'none'}
                                onChange={(e) => updateToolConfig(tIdx, { authType: e.target.value, mcpTools: [], allowedTools: [], ...resetApiVerification })}
                              >
                                <option value="none">None</option>
                                <option value="bearer">Bearer Token</option>
                                <option value="custom_header">Custom Header</option>
                              </select>
                            </div>
                            {(tool.config.authType || 'none') !== 'none' && (
                              <div>
                                <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>
                                  {(tool.config.authType || 'none') === 'custom_header' ? 'Header Value' : 'Bearer Token'}
                                </label>
                                <input
                                  type="password"
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                                  placeholder={(tool.config.authType || 'none') === 'custom_header' ? 'Header value' : 'Token'}
                                  value={tool.config.authToken || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { authToken: e.target.value, mcpTools: [], allowedTools: [], ...resetApiVerification })}
                                />
                              </div>
                            )}
                            {(tool.config.authType || 'none') === 'custom_header' && (
                              <div>
                                <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>Header Name</label>
                                <input
                                  type="text"
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                                  placeholder="e.g. X-API-Key"
                                  value={tool.config.authHeaderName || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { authHeaderName: e.target.value, mcpTools: [], allowedTools: [], ...resetApiVerification })}
                                />
                              </div>
                            )}
                          </div>
                          <div style={{ padding: '16px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(0,0,0,0.16)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' }}>
                              <div>
                                <label style={{ color: '#fff', display: 'block', marginBottom: '4px', fontSize: '13px', fontWeight: 700 }}>Allowed MCP Tools</label>
                                <div style={{ color: 'rgba(255,255,255,0.55)', fontSize: '12px' }}>
                                  Detect tools from this MCP server, then choose which ones this custom agent can use.
                                </div>
                              </div>
                              <button
                                type="button"
                                onClick={() => handleDetectMcpTools(tIdx)}
                                style={{ background: 'rgba(146, 0, 220, 0.2)', border: '1px solid #9200dc', color: '#fff', padding: '10px 16px', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}
                              >
                                {Array.isArray(tool.config.mcpTools) && tool.config.mcpTools.length ? 'Refresh Tools' : 'Detect Tools'}
                              </button>
                            </div>
                            {tool.config.verificationMessage && (
                              <div style={{ color: tool.config.verified ? '#86efac' : '#fca5a5', fontSize: '13px', fontWeight: 600, marginBottom: '12px' }}>
                                {tool.config.verificationMessage}
                              </div>
                            )}
                            {Array.isArray(tool.config.mcpTools) && tool.config.mcpTools.length > 0 ? (
                              <>
                                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '12px' }}>
                                  <button
                                    type="button"
                                    onClick={() => setMcpAllowedTools(tIdx, tool.config.mcpTools.map(item => item.name).filter(Boolean))}
                                    style={{ background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)', color: '#fff', padding: '7px 11px', borderRadius: '7px', cursor: 'pointer', fontSize: '12px' }}
                                  >
                                    Select All
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setMcpAllowedTools(tIdx, [])}
                                    style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.12)', color: '#fca5a5', padding: '7px 11px', borderRadius: '7px', cursor: 'pointer', fontSize: '12px' }}
                                  >
                                    Clear
                                  </button>
                                </div>
                                <div style={{ display: 'grid', gap: '10px', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
                                  {tool.config.mcpTools.map((mcpTool) => {
                                    const toolName = String(mcpTool.name || '').trim();
                                    const selectedTools = Array.isArray(tool.config.allowedTools)
                                      ? tool.config.allowedTools
                                      : String(tool.config.allowedTools || '').split(',').map(item => item.trim()).filter(Boolean);
                                    return (
                                      <label key={toolName} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', background: 'rgba(255,255,255,0.04)', border: selectedTools.includes(toolName) ? '1px solid rgba(134,239,172,0.45)' : '1px solid rgba(255,255,255,0.08)', borderRadius: '9px', padding: '10px', color: '#fff', cursor: 'pointer' }}>
                                        <input
                                          type="checkbox"
                                          checked={selectedTools.includes(toolName)}
                                          onChange={(e) => toggleMcpAllowedTool(tIdx, toolName, e.target.checked)}
                                          style={{ marginTop: '3px' }}
                                        />
                                        <span>
                                          <span style={{ display: 'block', fontSize: '13px', fontWeight: 700 }}>{mcpTool.title || toolName}</span>
                                          {mcpTool.title && mcpTool.title !== toolName && (
                                            <span style={{ display: 'block', color: 'rgba(255,255,255,0.55)', fontSize: '11px', marginTop: '2px' }}>{toolName}</span>
                                          )}
                                          {mcpTool.description && (
                                            <span style={{ display: 'block', color: 'rgba(255,255,255,0.65)', fontSize: '12px', marginTop: '5px', lineHeight: 1.4 }}>
                                              {String(mcpTool.description).slice(0, 180)}
                                            </span>
                                          )}
                                        </span>
                                      </label>
                                    );
                                  })}
                                </div>
                              </>
                            ) : (
                              <div style={{ color: 'rgba(255,255,255,0.55)', fontSize: '12px', padding: '10px', border: '1px dashed rgba(255,255,255,0.12)', borderRadius: '8px' }}>
                                No tools detected yet. Enter the MCP server URL and authentication details, then click Detect Tools.
                              </div>
                            )}
                          </div>
                        </div>
                      ) : isDatabaseToolConfig(tool) ? (
                        <div className="database-tool-config">
                          <div style={{ display: 'grid', gap: '16px', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginBottom: '20px' }}>
                            <div>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>Profile Name</label>
                              <input
                                type="text"
                                style={DATABASE_INPUT_STYLE}
                                placeholder="e.g. Billing DB"
                                value={tool.config.name || ''}
                                onChange={(e) => updateToolConfig(tIdx, { name: e.target.value, ...resetApiVerification })}
                              />
                            </div>
                            <div>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>Profile Purpose</label>
                              <input
                                type="text"
                                style={DATABASE_INPUT_STYLE}
                                placeholder="e.g. Customer orders and invoices"
                                value={tool.config.purpose || ''}
                                onChange={(e) => updateToolConfig(tIdx, { purpose: e.target.value })}
                              />
                            </div>
                            <div>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>Engine</label>
                              <select
                                style={DATABASE_INPUT_STYLE}
                                value={tool.config.engine || 'postgres'}
                                onChange={(e) => {
                                  const engineOption = getDatabaseEngineOption(e.target.value);
                                  updateToolConfig(tIdx, {
                                    engine: e.target.value,
                                    port: engineOption.defaultPort,
                                    detectedSchemas: [],
                                    selectedSchemas: [],
                                    selectedTables: [],
                                    ...resetApiVerification
                                  });
                                }}
                              >
                                {DATABASE_ENGINE_OPTIONS.map(option => (
                                  <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                              </select>
                            </div>
                            <div>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>Access Mode</label>
                              <select
                                style={DATABASE_INPUT_STYLE}
                                value={tool.config.permissionMode || 'read_only'}
                                onChange={(e) => updateToolConfig(tIdx, {
                                  permissionMode: e.target.value,
                                  selectedColumns: {},
                                  ...resetApiVerification
                                })}
                              >
                                {DATABASE_PERMISSION_OPTIONS.map(option => (
                                  <option key={option.value} value={option.value}>{option.label} ({option.description})</option>
                                ))}
                              </select>
                            </div>
                            <label style={{ color: '#fff', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '13px', marginTop: '30px' }}>
                              <input
                                type="checkbox"
                                checked={Boolean(tool.config.useConnectionUri)}
                                onChange={(e) => updateToolConfig(tIdx, {
                                  useConnectionUri: e.target.checked,
                                  detectedSchemas: [],
                                  selectedSchemas: [],
                                  selectedTables: [],
                                  ...resetApiVerification
                                })}
                              />
                              Use Connection String
                            </label>
                          </div>

                          {tool.config.useConnectionUri ? (
                            <div style={{ marginBottom: '20px' }}>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>
                                {String(tool.config.engine || 'postgres').toLowerCase() === 'sqlite' ? 'SQLite File URI' : String(tool.config.engine || 'postgres').toLowerCase() === 'odbc' ? 'ODBC Connection String' : 'Connection URI'}
                              </label>
                              <input
                                type="password"
                                style={{
                                  ...DATABASE_INPUT_STYLE,
                                  border: validationErrors.database ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)',
                                }}
                                placeholder={String(tool.config.engine || 'postgres').toLowerCase() === 'mysql' ? 'mysql://agent_reader:password@localhost:3306/app_db' : String(tool.config.engine || 'postgres').toLowerCase() === 'sqlite' ? 'file:C:/data/app.db?mode=ro' : String(tool.config.engine || 'postgres').toLowerCase() === 'odbc' ? 'DSN=AnalyticsReadonly;UID=agent_reader;PWD=password;' : 'postgresql://agent_reader:password@localhost:5432/app_db'}
                                value={tool.config.connectionUri || ''}
                                onChange={(e) => updateToolConfig(tIdx, {
                                  connectionUri: e.target.value,
                                  detectedSchemas: [],
                                  selectedSchemas: [],
                                  selectedTables: [],
                                  ...resetApiVerification
                                })}
                              />
                            </div>
                          ) : String(tool.config.engine || 'postgres').toLowerCase() === 'sqlite' ? (
                            <div style={{ marginBottom: '20px' }}>
                              <label style={DATABASE_FIELD_LABEL_STYLE}>SQLite File Path</label>
                              <input
                                type="text"
                                style={{ ...DATABASE_INPUT_STYLE, border: validationErrors.database && !(tool.config.filePath || tool.config.database || '').trim() ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)' }}
                                placeholder="C:/data/app.db"
                                value={tool.config.filePath || tool.config.database || ''}
                                onChange={(e) => updateToolConfig(tIdx, { filePath: e.target.value, database: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                              />
                            </div>
                          ) : String(tool.config.engine || 'postgres').toLowerCase() === 'odbc' ? (
                            <div style={{ display: 'grid', gap: '16px', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginBottom: '20px' }}>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>DSN Name</label>
                                <input
                                  type="text"
                                  style={{ ...DATABASE_INPUT_STYLE, border: validationErrors.database && !(tool.config.dsn || '').trim() ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)' }}
                                  placeholder="AnalyticsReadonly"
                                  value={tool.config.dsn || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { dsn: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Role Username</label>
                                <input
                                  type="text"
                                  style={DATABASE_INPUT_STYLE}
                                  placeholder="agent_reader"
                                  value={tool.config.username || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { username: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Role Password</label>
                                <input
                                  type="password"
                                  style={DATABASE_INPUT_STYLE}
                                  placeholder="Password for this DB role"
                                  value={tool.config.password || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { password: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                            </div>
                          ) : (
                            <div style={{ display: 'grid', gap: '16px', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginBottom: '20px' }}>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Host</label>
                                <input
                                  type="text"
                                  style={{ ...DATABASE_INPUT_STYLE, border: validationErrors.database && !(tool.config.host || '').trim() ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)' }}
                                  placeholder="localhost or db.company.com"
                                  value={tool.config.host || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { host: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Port</label>
                                <input
                                  type="text"
                                  inputMode="numeric"
                                  pattern="[0-9]*"
                                  style={DATABASE_INPUT_STYLE}
                                  value={tool.config.port || '5432'}
                                  onChange={(e) => updateToolConfig(tIdx, { port: e.target.value.replace(/\D/g, ''), detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>{String(tool.config.engine || 'postgres').toLowerCase() === 'oracle' ? 'Database / Service' : 'Database Name'}</label>
                                <input
                                  type="text"
                                  style={{ ...DATABASE_INPUT_STYLE, border: validationErrors.database && !(tool.config.database || '').trim() ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)' }}
                                  placeholder="app_db"
                                  value={tool.config.database || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { database: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              {String(tool.config.engine || 'postgres').toLowerCase() === 'oracle' && (
                                <div>
                                  <label style={DATABASE_FIELD_LABEL_STYLE}>Oracle Service</label>
                                  <input
                                    type="text"
                                    style={DATABASE_INPUT_STYLE}
                                    placeholder="orclpdb1"
                                    value={tool.config.serviceName || ''}
                                    onChange={(e) => updateToolConfig(tIdx, { serviceName: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                  />
                                </div>
                              )}
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Role Username</label>
                                <input
                                  type="text"
                                  style={{ ...DATABASE_INPUT_STYLE, border: validationErrors.database && !(tool.config.username || '').trim() ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)' }}
                                  placeholder="agent_reader"
                                  value={tool.config.username || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { username: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>Role Password</label>
                                <input
                                  type="password"
                                  style={DATABASE_INPUT_STYLE}
                                  placeholder="Password for this DB role"
                                  value={tool.config.password || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { password: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                />
                              </div>
                              {String(tool.config.engine || 'postgres').toLowerCase() === 'sqlserver' && (
                                <div>
                                  <label style={DATABASE_FIELD_LABEL_STYLE}>SQL Server Driver</label>
                                  <input
                                    type="text"
                                    style={DATABASE_INPUT_STYLE}
                                    placeholder="ODBC Driver 18 for SQL Server"
                                    value={tool.config.driver || ''}
                                    onChange={(e) => updateToolConfig(tIdx, { driver: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                  />
                                </div>
                              )}
                              {String(tool.config.engine || 'postgres').toLowerCase() === 'postgres' && (
                              <div>
                                <label style={DATABASE_FIELD_LABEL_STYLE}>SSL Mode</label>
                                <select
                                  style={DATABASE_INPUT_STYLE}
                                  value={tool.config.sslmode || 'prefer'}
                                  onChange={(e) => updateToolConfig(tIdx, { sslmode: e.target.value, detectedSchemas: [], selectedSchemas: [], selectedTables: [], ...resetApiVerification })}
                                >
                                  {[
                                    ['disable', 'Disable'],
                                    ['allow', 'Allow'],
                                    ['prefer', 'Prefer'],
                                    ['require', 'Require'],
                                    ['verify-ca', 'Verify CA'],
                                    ['verify-full', 'Verify Full'],
                                  ].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                </select>
                              </div>
                              )}
                            </div>
                          )}

                          <div style={DATABASE_SECTION_TITLE_STYLE}>Safety Limits</div>
                          <div style={{ display: 'grid', gap: '12px', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', marginBottom: '20px' }}>
                            <div>
                              <label style={DATABASE_LIMIT_LABEL_STYLE}>Result Row Limit</label>
                              <input
                                type="number"
                                min="1"
                                max="1000"
                                style={{ ...DATABASE_INPUT_STYLE, padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                value={tool.config.limits?.maxRows || 100}
                                onChange={(e) => updateToolConfig(tIdx, { limits: { ...(tool.config.limits || {}), maxRows: e.target.value } })}
                              />
                            </div>
                            <div>
                              <label style={DATABASE_LIMIT_LABEL_STYLE}>Query Timeout (Sec)</label>
                              <input
                                type="number"
                                min="1"
                                max="120"
                                style={{ ...DATABASE_INPUT_STYLE, padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                value={tool.config.limits?.timeoutSeconds || 15}
                                onChange={(e) => updateToolConfig(tIdx, { limits: { ...(tool.config.limits || {}), timeoutSeconds: e.target.value } })}
                              />
                            </div>
                            <div>
                              <label style={DATABASE_LIMIT_LABEL_STYLE}>Result Size Limit (Bytes)</label>
                              <input
                                type="number"
                                min="1000"
                                max="200000"
                                style={{ ...DATABASE_INPUT_STYLE, padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                value={tool.config.limits?.maxResultBytes || 12000}
                                onChange={(e) => updateToolConfig(tIdx, { limits: { ...(tool.config.limits || {}), maxResultBytes: e.target.value } })}
                              />
                            </div>
                            <div>
                              <label style={DATABASE_LIMIT_LABEL_STYLE}>Write Row Limit</label>
                              <input
                                type="number"
                                min="1"
                                max="1000"
                                style={{ ...DATABASE_INPUT_STYLE, padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                value={tool.config.limits?.maxAffectedRows || 10}
                                onChange={(e) => updateToolConfig(tIdx, { limits: { ...(tool.config.limits || {}), maxAffectedRows: e.target.value } })}
                              />
                            </div>
                          </div>

                          <div style={{ padding: '16px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(0,0,0,0.16)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '12px' }}>
                              <div>
                                <label style={{ color: '#fff', display: 'block', marginBottom: '4px', fontSize: '13px', fontWeight: 700 }}>Accessible Schemas</label>
                                <div style={{ color: 'rgba(255,255,255,0.55)', fontSize: '12px' }}>
                                  Test with the DB role, then choose the schemas and optional tables this agent may query.
                                </div>
                              </div>
                              <button
                                type="button"
                                onClick={() => handleTestDatabaseConnection(tIdx)}
                                style={{ background: 'rgba(146, 0, 220, 0.2)', border: '1px solid #9200dc', color: '#fff', padding: '10px 16px', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}
                              >
                                {Array.isArray(tool.config.detectedSchemas) && tool.config.detectedSchemas.length ? 'Refresh Schemas' : 'Test Connection'}
                              </button>
                            </div>
                            {tool.config.verificationMessage && (
                              <div style={{ color: tool.config.verified ? '#86efac' : '#fca5a5', fontSize: '13px', fontWeight: 600, marginBottom: '12px' }}>
                                {tool.config.verificationMessage}
                              </div>
                            )}
                            {validationErrors.database && (
                              <div style={{ color: '#ef4444', fontSize: '12px', marginBottom: '12px' }}>{validationErrors.database}</div>
                            )}
                            {Array.isArray(tool.config.detectedSchemas) && tool.config.detectedSchemas.length > 0 ? (
                              <div style={{ display: 'grid', gap: '12px' }}>
                                {tool.config.detectedSchemas.map(schema => {
                                  const schemaName = schema.name;
                                  const selectedSchemas = Array.isArray(tool.config.selectedSchemas) ? tool.config.selectedSchemas : [];
                                  const selectedTables = Array.isArray(tool.config.selectedTables) ? tool.config.selectedTables : [];
                                  const schemaSelected = selectedSchemas.includes(schemaName);
                                  return (
                                    <div key={schemaName} style={{ background: 'rgba(255,255,255,0.04)', border: schemaSelected ? '1px solid rgba(134,239,172,0.45)' : '1px solid rgba(255,255,255,0.08)', borderRadius: '9px', padding: '12px' }}>
                                      <label style={{ display: 'flex', alignItems: 'center', gap: '10px', color: '#fff', cursor: 'pointer', fontWeight: 700 }}>
                                        <input
                                          type="checkbox"
                                          checked={schemaSelected}
                                          onChange={(e) => toggleDatabaseSchema(tIdx, schemaName, e.target.checked)}
                                        />
                                        <span>{schemaName}</span>
                                        <span style={{ color: 'rgba(255,255,255,0.5)', fontSize: '12px', fontWeight: 500 }}>
                                          {(schema.tables || []).length} table{(schema.tables || []).length === 1 ? '' : 's'}
                                        </span>
                                      </label>
                                      {schemaSelected && Array.isArray(schema.tables) && schema.tables.length > 0 && (
                                        <div style={{ display: 'grid', gap: '8px', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginTop: '10px' }}>
                                          {schema.tables.map(table => {
                                            const tableRef = `${schemaName}.${table.name}`;
                                            const tableSelected = selectedTables.includes(tableRef);
                                            const selectedColumns = tool.config.selectedColumns || {};
                                            const writableColumns = Array.isArray(selectedColumns[tableRef]) ? selectedColumns[tableRef] : [];
                                            const showWritableColumns = tableSelected && databaseModeAllowsColumnWrites(tool.config.permissionMode);
                                            return (
                                              <div key={tableRef} style={{ color: '#fff', fontSize: '12px', background: 'rgba(0,0,0,0.16)', border: tableSelected ? '1px solid rgba(134,239,172,0.38)' : '1px solid rgba(255,255,255,0.06)', borderRadius: '7px', padding: '8px' }}>
                                                <label style={{ display: 'grid', gridTemplateColumns: '18px minmax(0, 1fr)', gap: '8px', cursor: 'pointer' }}>
                                                  <input
                                                    type="checkbox"
                                                    checked={tableSelected}
                                                    onChange={(e) => toggleDatabaseTable(tIdx, schemaName, table.name, e.target.checked)}
                                                    style={{ marginTop: '2px' }}
                                                  />
                                                  <span style={{ minWidth: 0 }}>
                                                    <strong style={{ display: 'block', overflowWrap: 'anywhere' }}>{table.name}</strong>
                                                    <span style={{ color: 'rgba(255,255,255,0.52)', display: 'block', marginTop: '2px' }}>
                                                      {(table.columns || []).slice(0, 6).map(col => col.name).join(', ') || table.type || 'table'}
                                                    </span>
                                                  </span>
                                                </label>
                                                {showWritableColumns && Array.isArray(table.columns) && table.columns.length > 0 && (
                                                  <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                                                    <div style={{ color: 'rgba(255,255,255,0.58)', fontSize: '11px', marginBottom: '6px' }}>Writable columns</div>
                                                    <div style={{ display: 'grid', gap: '6px' }}>
                                                      {table.columns.map(column => (
                                                        <label key={`${tableRef}.${column.name}`} style={{ display: 'flex', alignItems: 'center', gap: '7px', color: 'rgba(255,255,255,0.82)', cursor: 'pointer' }}>
                                                          <input
                                                            type="checkbox"
                                                            checked={writableColumns.includes(column.name)}
                                                            onChange={(e) => toggleDatabaseColumn(tIdx, tableRef, column.name, e.target.checked)}
                                                          />
                                                          <span style={{ overflowWrap: 'anywhere' }}>{column.name}</span>
                                                          <span style={{ color: 'rgba(255,255,255,0.42)', fontSize: '10px' }}>{column.type}</span>
                                                        </label>
                                                      ))}
                                                    </div>
                                                  </div>
                                                )}
                                              </div>
                                            );
                                          })}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })}
                              </div>
                            ) : (
                              <div style={{ color: 'rgba(255,255,255,0.55)', fontSize: '12px', padding: '10px', border: '1px dashed rgba(255,255,255,0.12)', borderRadius: '8px' }}>
                                No schemas detected yet. Enter the connection details and DB role credentials, then click Test Connection.
                              </div>
                            )}
                          </div>
                        </div>
                      ) : tool.type === 'api' ? (
                        <div className="api-tool-config">
                          <div style={{ marginBottom: '20px' }}>
                            <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>Connector Type</label>
                            <select
                              style={{ width: '100%', maxWidth: '260px', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                              value={tool.config.kind || 'rest'}
                              onChange={(e) => updateToolConfig(tIdx, {
                                kind: e.target.value,
                                method: tool.config.method || 'GET',
                                parameters: tool.config.parameters || [],
                                ...resetApiVerification
                              })}
                            >
                              <option value="rest">REST Endpoint</option>
                            </select>
                          </div>
                          <div style={{ display: 'flex', gap: '15px', marginBottom: '20px' }}>
                            <div style={{ flex: 2 }}>
                              <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>{String(tool.config.kind || 'rest').toLowerCase() === 'mcp' ? 'MCP Server URL' : 'API Endpoint URL'}</label>
                              <input
                                list={`url-suggestions-${tIdx}`}
                                type="text"
                                style={{
                                  width: '100%',
                                  background: 'rgba(0,0,0,0.2)',
                                  border: (validationErrors.api && !(tool.config.url || "").trim()) ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)',
                                  color: '#fff',
                                  padding: '10px',
                                  borderRadius: '8px'
                                }}
                                placeholder="e.g. http://localhost:5000/api/leave"
                                value={tool.config.url}
                                onChange={(e) => updateToolConfig(tIdx, { url: e.target.value, ...resetApiVerification })}
                              />
                              {validationErrors.api && !(tool.config.url || "").trim() && (
                                <span style={{ color: '#ef4444', fontSize: '11px', marginTop: '6px', display: 'block' }}>
                                  {validationErrors.api}
                                </span>
                              )}
                              <datalist id={`url-suggestions-${tIdx}`}>
                                <option value="http://localhost:5000/api/leave" />
                                <option value="https://api.company.com/leave" />
                                <option value="http://localhost:5000/api/salary" />
                                <option value="http://localhost:5000/api/employee" />
                              </datalist>
                            </div>
                            {String(tool.config.kind || 'rest').toLowerCase() !== 'mcp' && (
                            <div style={{ flex: 1 }}>
                              <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>HTTP Method</label>
                              <select
                                style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }}
                                value={tool.config.method}
                                onChange={(e) => updateToolConfig(tIdx, { method: e.target.value, ...resetApiVerification })}
                              >
                                {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map(m => <option key={m} value={m}>{m}</option>)}
                              </select>
                            </div>
                            )}
                          </div>

                          {String(tool.config.kind || 'rest').toLowerCase() === 'mcp' && (
                            <div style={{ display: 'grid', gap: '14px', marginBottom: '20px' }}>
                              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 1fr) minmax(180px, 1fr)', gap: '12px' }}>
                                <div>
                                  <label style={{ color: '#fff', opacity: 0.65, fontSize: '11px', display: 'block', marginBottom: '6px' }}>Server Name</label>
                                  <input
                                    type="text"
                                    placeholder="e.g. github"
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                    value={tool.config.name || ''}
                                    onChange={(e) => updateToolConfig(tIdx, { name: e.target.value, ...resetApiVerification })}
                                  />
                                </div>
                                <div>
                                  <label style={{ color: '#fff', opacity: 0.65, fontSize: '11px', display: 'block', marginBottom: '6px' }}>Auth Type</label>
                                  <select
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                    value={tool.config.authType || 'none'}
                                    onChange={(e) => updateToolConfig(tIdx, { authType: e.target.value, ...resetApiVerification })}
                                  >
                                    <option value="none">None</option>
                                    <option value="bearer">Bearer Token</option>
                                    <option value="api_key">Custom Header</option>
                                  </select>
                                </div>
                              </div>
                              {tool.config.authType && tool.config.authType !== 'none' && (
                                <div style={{ display: 'grid', gridTemplateColumns: tool.config.authType === 'api_key' ? 'minmax(160px, 1fr) minmax(220px, 2fr)' : '1fr', gap: '12px' }}>
                                  {tool.config.authType === 'api_key' && (
                                    <div>
                                      <label style={{ color: '#fff', opacity: 0.65, fontSize: '11px', display: 'block', marginBottom: '6px' }}>Header Name</label>
                                      <input
                                        type="text"
                                        placeholder="e.g. X-API-Key"
                                        style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                        value={tool.config.authHeaderName || ''}
                                        onChange={(e) => updateToolConfig(tIdx, { authHeaderName: e.target.value, ...resetApiVerification })}
                                      />
                                    </div>
                                  )}
                                  <div>
                                    <label style={{ color: '#fff', opacity: 0.65, fontSize: '11px', display: 'block', marginBottom: '6px' }}>Token</label>
                                    <input
                                      type="password"
                                      placeholder="Token"
                                      style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                      value={tool.config.authToken || ''}
                                      onChange={(e) => updateToolConfig(tIdx, { authToken: e.target.value, ...resetApiVerification })}
                                    />
                                  </div>
                                </div>
                              )}
                              {tool.config.mcpTools?.length > 0 && (
                                <div style={{ border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', padding: '12px', background: 'rgba(0,0,0,0.14)' }}>
                                  <div style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', marginBottom: '10px' }}>Allowed MCP Tools</div>
                                  <div style={{ display: 'grid', gap: '10px' }}>
                                    {tool.config.mcpTools.map(mcpTool => {
                                      const isAllowed = (tool.config.allowedTools || []).includes(mcpTool.name);
                                      return (
                                        <div key={mcpTool.name} style={{ paddingBottom: '8px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                                          <label style={{ color: '#fff', display: 'grid', gridTemplateColumns: '18px minmax(0, 1fr)', alignItems: 'start', columnGap: '10px', width: '100%', fontSize: '13px', lineHeight: 1.3, cursor: 'pointer', textAlign: 'left', margin: 0 }}>
                                            <input
                                              type="checkbox"
                                              checked={isAllowed}
                                              onChange={() => toggleMcpAllowedTool(tIdx, mcpTool.name, !isAllowed)}
                                              style={{ width: '16px', height: '16px', margin: '2px 0 0', padding: 0, justifySelf: 'start', flexShrink: 0 }}
                                            />
                                            <span style={{ display: 'block', minWidth: 0 }}>
                                              <strong style={{ display: 'block', color: '#fff', fontWeight: 700, overflowWrap: 'anywhere' }}>{mcpTool.name}</strong>
                                              {mcpTool.description && <span style={{ display: 'block', color: 'rgba(255,255,255,0.58)', marginTop: '2px', overflowWrap: 'anywhere' }}>{mcpTool.description}</span>}
                                            </span>
                                          </label>
                                          {isAllowed && (
                                            <div style={{ marginTop: '10px', marginLeft: '28px' }}>
                                              <RoutingProfileEditor
                                                title={`Routing guidance for ${mcpTool.name}`}
                                                value={mcpTool.routing_profile || mcpTool.routingProfile}
                                                onChange={(routingProfile) => updateMcpToolRoutingProfile(tIdx, mcpTool.name, routingProfile)}
                                              />
                                            </div>
                                          )}
                                        </div>
                                      );
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                          {String(tool.config.kind || 'rest').toLowerCase() !== 'mcp' && (
                          <div className="parameters-config">
                            <h5 style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', marginBottom: '15px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <FaPlug size={12} /> API Parameters
                            </h5>
                            {tool.config.parameters?.map((param, pIdx) => (
                              <div key={pIdx} style={{ display: 'flex', gap: '8px', marginBottom: '12px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                                <div style={{ flex: '1 1 120px' }}>
                                  <label style={{ color: '#fff', opacity: 0.6, fontSize: '11px', display: 'block', marginBottom: '5px' }}>Location</label>
                                  <select
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                    value={param.location || 'query'}
                                    onChange={(e) => updateParameter(tIdx, pIdx, 'location', e.target.value)}
                                  >
                                    <option value="path">Path Param</option>
                                    <option value="query">Query Param</option>
                                    <option value="body">Body (JSON)</option>
                                    <option value="header">Header</option>
                                  </select>
                                </div>
                                <div style={{ flex: '2 1 150px' }}>
                                  <label style={{ color: '#fff', opacity: 0.6, fontSize: '11px', display: 'block', marginBottom: '5px' }}>Key</label>
                                  <input
                                    list={`param-keys-${tIdx}`}
                                    type="text"
                                    placeholder="e.g. user_id"
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                    value={param.key}
                                    onChange={(e) => updateParameter(tIdx, pIdx, 'key', e.target.value)}
                                  />
                                  <datalist id={`param-keys-${tIdx}`}>
                                    <option value="employee_id" />
                                    <option value="user_id" />
                                    <option value="company_id" />
                                    <option value="leave_type" />
                                    <option value="start_date" />
                                    <option value="end_date" />
                                    <option value="reason" />
                                    <option value="Authorization" />
                                    <option value="Content-Type" />
                                  </datalist>
                                </div>
                                <div style={{ flex: '2 1 150px' }}>
                                  <label style={{ color: '#fff', opacity: 0.6, fontSize: '11px', display: 'block', marginBottom: '5px' }}>Default Value</label>
                                  <input
                                    type="text"
                                    placeholder="e.g. 123"
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '13px' }}
                                    value={param.value}
                                    onChange={(e) => updateParameter(tIdx, pIdx, 'value', e.target.value)}
                                  />
                                </div>
                                <button onClick={() => removeParameter(tIdx, pIdx)} style={{ background: 'none', border: 'none', color: '#f87171', padding: '10px', cursor: 'pointer' }} title="Remove Parameter">✕</button>
                              </div>
                            ))}
                            {getSelectedApiParameterHelp(tool.config.parameters || []).length > 0 && (
                              <div style={{ marginBottom: '12px', display: 'grid', gap: '8px' }}>
                                {getSelectedApiParameterHelp(tool.config.parameters || []).map(help => (
                                  <div key={help.label} style={{ color: 'rgba(255,255,255,0.62)', fontSize: '12px', lineHeight: 1.45, padding: '10px 12px', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '8px', background: 'rgba(255,255,255,0.03)' }}>
                                    <strong style={{ color: 'rgba(255,255,255,0.82)' }}>{help.label}:</strong> {help.description}
                                  </div>
                                ))}
                              </div>
                            )}
                            <button
                              onClick={() => addParameter(tIdx)}
                              style={{ background: 'rgba(255,255,255,0.03)', border: '1px dashed rgba(255,255,255,0.2)', color: '#aaa', padding: '10px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '12px', width: '100%', textAlign: 'center', transition: 'all 0.3s' }}
                              onMouseOver={(e) => { e.target.style.background = 'rgba(255,255,255,0.08)'; e.target.style.color = '#fff'; }}
                              onMouseOut={(e) => { e.target.style.background = 'rgba(255,255,255,0.03)'; e.target.style.color = '#aaa'; }}
                            >
                              + Add API Parameter
                            </button>
                          </div>
                          )}
                          <div style={{ marginTop: '20px', padding: '16px', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(0,0,0,0.16)' }}>
                            <h5 style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', margin: '0 0 12px' }}>{String(tool.config.kind || 'rest').toLowerCase() === 'mcp' ? 'Verify MCP Server' : 'Verify Endpoint'}</h5>
                            {String(tool.config.kind || 'rest').toLowerCase() !== 'mcp' && (
                              <>
                                <label style={{ color: '#fff', opacity: 0.65, fontSize: '11px', display: 'block', marginBottom: '6px' }}>Sample user request</label>
                                <textarea
                                  rows={3}
                                  placeholder="e.g. Show me the leave balance for employee 123"
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px', resize: 'vertical', fontSize: '13px' }}
                                  value={tool.config.testPrompt || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { testPrompt: e.target.value, ...resetApiVerification })}
                                />
                                <div style={{ color: 'rgba(255,255,255,0.55)', fontSize: '12px', marginTop: '6px' }}>
                                  Type this like a user would ask the agent. The system will infer the configured API parameters from it.
                                </div>
                              </>
                            )}
                            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '12px', flexWrap: 'wrap' }}>
                              <button
                                type="button"
                                onClick={() => handleVerifyApiTool(tIdx)}
                                style={{ background: 'rgba(146, 0, 220, 0.2)', border: '1px solid #9200dc', color: '#fff', padding: '10px 16px', borderRadius: '8px', cursor: 'pointer', fontWeight: 600 }}
                              >
                                {String(tool.config.kind || 'rest').toLowerCase() === 'mcp' ? 'Verify Server' : 'Verify Endpoint'}
                              </button>
                              {tool.config.verificationMessage && (
                                <span style={{ color: tool.config.verified ? '#86efac' : '#fca5a5', fontSize: '13px', fontWeight: 600 }}>
                                  {tool.config.verificationMessage}
                                </span>
                              )}
                            </div>
                            {tool.config.verified && (tool.config.verificationResult?.extracted_value !== undefined || tool.config.verificationResult?.response_preview !== undefined) && (
                              <div style={{ marginTop: '12px' }}>
                                <div style={{ color: 'rgba(255,255,255,0.65)', fontSize: '11px', marginBottom: '6px' }}>Returned value</div>
                                <pre style={{ margin: 0, maxHeight: '180px', overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'rgba(0,0,0,0.28)', border: '1px solid rgba(255,255,255,0.08)', color: '#d8f3dc', padding: '10px', borderRadius: '8px', fontSize: '12px', lineHeight: 1.45 }}>
                                  {formatApiVerificationPreview(tool.config.verificationResult.extracted_value || tool.config.verificationResult.response_preview)}
                                </pre>
                              </div>
                            )}
                          </div>
                        </div>
                      ) : (
                        <div className="form-tool-config">
                          <h5 style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', marginBottom: '15px' }}>Intent Trigger</h5>
                          <div style={{ display: 'grid', gap: '12px', marginBottom: '18px' }}>
                            <div>
                              <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Trigger Description *</label>
                              <input
                                type="text"
                                placeholder="e.g. Show when user wants to book an appointment"
                                style={{
                                  width: '100%',
                                  background: 'rgba(0,0,0,0.2)',
                                  border: (validationErrors.formTrigger && !(tool.config.triggerDescription || "").trim()) ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)',
                                  color: '#fff',
                                  padding: '8px',
                                  borderRadius: '6px'
                                }}
                                value={tool.config.triggerDescription || ''}
                                onChange={(e) => updateToolConfig(tIdx, { triggerDescription: e.target.value })}
                              />
                              {validationErrors.formTrigger && !(tool.config.triggerDescription || "").trim() && (
                                <span style={{ color: '#ef4444', fontSize: '11px', marginTop: '6px', display: 'block' }}>
                                  {validationErrors.formTrigger}
                                </span>
                              )}
                            </div>
                            <div>
                              <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Trigger Keywords</label>
                              <input
                                type="text"
                                placeholder="book, appointment, schedule"
                                style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                value={Array.isArray(tool.config.triggerKeywords) ? tool.config.triggerKeywords.join(', ') : tool.config.triggerKeywords || ''}
                                onChange={(e) => updateToolConfig(tIdx, { triggerKeywords: e.target.value })}
                              />
                            </div>
                            <div>
                              <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Confirmation Message</label>
                              <input
                                type="text"
                                placeholder="Please complete the appointment booking form below."
                                style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                value={tool.config.confirmationMessage || ''}
                                onChange={(e) => updateToolConfig(tIdx, { confirmationMessage: e.target.value })}
                              />
                            </div>
                          </div>
                          <h5 style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', marginBottom: '15px' }}>Submission Destination</h5>
                          <div style={{ display: 'flex', gap: '10px', marginBottom: '18px', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                            <div style={{ minWidth: '260px' }}>
                              <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '8px' }}>After Submit</label>
                              <div className="form-submit-option-grid">
                                {[
                                  { value: 'email', label: 'Email company admin' },
                                  { value: 'api', label: 'POST to API endpoint' }
                                ].map(option => {
                                  const selectedActions = getFormSubmitActions(tool.config);
                                  const isChecked = selectedActions.includes(option.value);
                                  return (
                                    <label key={option.value} className={`form-submit-option ${isChecked ? 'form-submit-option--active' : ''}`}>
                                      <input
                                        type="checkbox"
                                        checked={isChecked}
                                        onChange={(e) => {
                                          let nextActions = e.target.checked
                                            ? [...new Set([...selectedActions, option.value])]
                                            : selectedActions.filter(action => action !== option.value);
                                          if (!nextActions.length) {
                                            nextActions = ['email'];
                                          }
                                          updateToolConfig(tIdx, {
                                            submitActions: nextActions,
                                            submitAction: nextActions[0]
                                          });
                                        }}
                                      />
                                      <span className="form-submit-option-check" aria-hidden="true"></span>
                                      <span>{option.label}</span>
                                    </label>
                                  );
                                })}
                              </div>
                            </div>
                            {getFormSubmitActions(tool.config).includes('api') && (
                              <div style={{ flex: 1, minWidth: '260px' }}>
                                <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>API Endpoint</label>
                                <input
                                  type="url"
                                  placeholder="https://example.com/form-submit"
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                  value={tool.config.apiEndpoint || ''}
                                  onChange={(e) => updateToolConfig(tIdx, { apiEndpoint: e.target.value })}
                                />
                              </div>
                            )}
                          </div>
                          <h5 style={{ color: '#fff', opacity: 0.8, textTransform: 'uppercase', fontSize: '12px', marginBottom: '15px' }}>Form Fields</h5>
                          {tool.config.fields?.map((field, fIdx) => (
                            <div key={fIdx} style={{ display: 'flex', gap: '10px', marginBottom: '10px', alignItems: 'flex-end' }}>
                              <div style={{ flex: 1 }}>
                                <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Label</label>
                                <input type="text" style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }} value={field.label} onChange={(e) => updateField(tIdx, fIdx, 'label', e.target.value)} />
                              </div>
                              <div style={{ width: '140px' }}>
                                <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Input Type</label>
                                <select
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                  value={field.inputType || field.type || 'text'}
                                  onChange={(e) => updateField(tIdx, fIdx, 'inputType', e.target.value)}
                                >
                                  <option value="text">Text</option>
                                  <option value="textarea">Textarea</option>
                                  <option value="dropdown">Dropdown</option>
                                  <option value="radio">Radio Buttons</option>
                                  <option value="checkbox">Checkbox</option>
                                  <option value="email">Email</option>
                                  <option value="number">Number</option>
                                  <option value="phone">Phone</option>
                                  <option value="date">Date</option>
                                </select>
                              </div>
                              <div style={{ width: '135px' }}>
                                <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Data Type</label>
                                <select
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                  value={field.dataType || 'string'}
                                  onChange={(e) => updateField(tIdx, fIdx, 'dataType', e.target.value)}
                                >
                                  <option value="string">String</option>
                                  <option value="email">Email</option>
                                  <option value="number">Number</option>
                                  <option value="phone">Phone</option>
                                  <option value="date">Date</option>
                                  <option value="boolean">Boolean</option>
                                </select>
                              </div>
                              <div style={{ width: '120px' }}>
                                <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Required</label>
                                <select
                                  style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                  value={field.required || 'Optional'}
                                  onChange={(e) => updateField(tIdx, fIdx, 'required', e.target.value)}
                                >
                                  <option value="Optional">Optional</option>
                                  <option value="Required">Required</option>
                                </select>
                              </div>
                              {['dropdown', 'radio'].includes((field.inputType || field.type || '').toLowerCase()) && (
                                <div style={{ minWidth: '180px', flex: 1 }}>
                                  <label style={{ color: '#fff', fontSize: '11px', display: 'block', marginBottom: '5px' }}>Options</label>
                                  <input
                                    type="text"
                                    style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px' }}
                                    placeholder="Option A, Option B"
                                    value={Array.isArray(field.options) ? field.options.join(', ') : field.options || ''}
                                    onChange={(e) => updateField(tIdx, fIdx, 'options', e.target.value)}
                                  />
                                </div>
                              )}
                              <button onClick={() => removeField(tIdx, fIdx)} style={{ background: 'none', border: 'none', color: '#f87171', padding: '10px' }}>✕</button>
                            </div>
                          ))}
                          <button onClick={() => addField(tIdx)} style={{ background: 'none', border: '1px dashed rgba(255,255,255,0.2)', color: '#aaa', padding: '8px 16px', borderRadius: '6px', cursor: 'pointer', fontSize: '12px' }}>+ Add Field</button>
                        </div>
                      )}
                    </div>
                  ))}
                    <div style={{ display: 'flex', gap: '15px', marginTop: '10px' }}>
                      {hasApiSelected && (
                        <button
                          type="button"
                          onClick={addApiTool}
                          style={{
                            background: 'rgba(146, 0, 220, 0.1)',
                            border: '1px dashed #9200dc',
                            color: '#fff',
                            padding: '10px 20px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            fontSize: '13px',
                            fontWeight: '600'
                          }}
                        >
                          + Add Another API Endpoint
                        </button>
                      )}
                      {hasFormSelected && (
                        <button
                          type="button"
                          onClick={addFormTool}
                          style={{
                            background: 'rgba(146, 0, 220, 0.1)',
                            border: '1px dashed #9200dc',
                            color: '#fff',
                            padding: '10px 20px',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            fontSize: '13px',
                            fontWeight: '600'
                          }}
                        >
                          + Add Another Form
                        </button>
                      )}
                    </div>
                  </div>
              </div>
              )}

              {/* Knowledge Base Section — only visible when KB is checked */}
              {hasKbSelected && (
                <div className="agent-form-section-custom" style={{ marginBottom: '32px' }}>
                  <h3 style={{ color: '#b026ff', fontSize: '14px', textTransform: 'uppercase', letterSpacing: '2px', marginBottom: '25px' }}>SECTION 3: Knowledge Base Configuration</h3>
                  <RoutingProfileEditor
                    title="Knowledge base routing guidance"
                    value={agentForm.kbRoutingProfile}
                    onChange={(kbRoutingProfile) => setAgentForm(prev => ({ ...prev, kbRoutingProfile }))}
                  />
                   {validationErrors.kb && (
                    <span style={{ color: '#ef4444', fontSize: '11px', display: 'block', marginBottom: '15px' }}>
                      {validationErrors.kb}
                    </span>
                  )}
                  <div className="kb-links-list">
                    {agentForm.knowledgeBase.map((kb, kIdx) => (
                      <div key={kIdx} style={{ marginBottom: '15px' }}>
                      <div className="kb-link-row" style={{ display: 'flex', gap: '10px' }}>
                        <select style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '8px' }} value={kb.type} onChange={(e) => updateKBLink(kIdx, 'type', e.target.value)}>
                          <option value="onedrive" disabled={!allowsOneDrive}>OneDrive File/Folder {!allowsOneDrive && "(Business Tier Required)"}</option>
                          <option value="sharepoint" disabled={!allowsSharePoint}>SharePoint File/Folder {!allowsSharePoint && "(Plan Required)"}</option>
                          <option value="website">Website URL</option>
                        </select>
                        <input
                          type="text"
                          style={{
                            flex: 1,
                            background: 'rgba(0,0,0,0.2)',
                            border: (validationErrors.kb && !(kb.value || "").trim()) ? '1.5px solid #ef4444' : '1px solid rgba(255,255,255,0.1)',
                            color: '#fff',
                            padding: '10px',
                            borderRadius: '8px'
                          }}
                          placeholder={
                            kb.type === 'onedrive'
                              ? "Paste OneDrive file or folder link..."
                              : kb.type === 'sharepoint'
                                ? "Paste SharePoint file, folder, document library, or site page link..."
                                : "e.g. http://localhost:5000 or https://example.com/docs"
                          }
                          value={kb.value}
                          onChange={(e) => updateKBLink(kIdx, 'value', e.target.value)}
                        />
                        <button
                          type="button"
                          onClick={() => {
                            if (!agentForm.id) {
                              showMessage("This resource will automatically sync once you click 'Create Agent'.", "info");
                            } else {
                              syncKnowledgeBaseSource(kb, kIdx);
                            }
                          }}
                          disabled={syncingKbIndex !== null || loading || !(kb.value || "").trim()}
                          title={!agentForm.id ? "Will auto-sync on creation" : `Sync ${kb.type === 'onedrive' ? 'OneDrive file/folder' : kb.type === 'sharepoint' ? 'SharePoint source' : 'website'} now`}
                          style={{
                            background: 'rgba(74, 222, 128, 0.12)',
                            border: '1px solid rgba(74, 222, 128, 0.35)',
                            color: '#4ade80',
                            width: '42px', minWidth: '42px', height: '42px',
                            borderRadius: '8px',
                            cursor: syncingKbIndex !== null || loading || !(kb.value || "").trim() ? 'not-allowed' : 'pointer',
                            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                            opacity: syncingKbIndex !== null || loading || !(kb.value || "").trim() ? 0.55 : 1,
                          }}
                        >
                          <FaSyncAlt style={{ animation: syncingKbIndex === kIdx ? 'spin 1s linear infinite' : 'none' }} />
                        </button>
                        <button onClick={() => removeKBLink(kIdx)} style={{ background: 'none', border: 'none', color: '#f87171' }}>✕</button>
                      </div>
                      {kb.type === 'website' && (() => {
                        const crawl = normalizeWebCrawlSettings(kb.crawl || {});
                        const setCrawl = (patch) => updateKBLink(kIdx, 'crawl', { ...crawl, ...patch });
                        return (
                          <div style={{ marginTop: '10px', padding: '12px', background: 'rgba(146, 0, 220, 0.06)', border: '1px solid rgba(146, 0, 220, 0.18)', borderRadius: '8px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px', alignItems: 'end' }}>
                            <label title={WEB_CRAWL_HELP.maxPages} style={{ color: '#cbd5e1', fontSize: '12px', fontWeight: 600, cursor: 'help' }}>
                              Max pages
                              <input
                                type="number"
                                min="1"
                                max="100"
                                value={crawl.maxPages}
                                title={WEB_CRAWL_HELP.maxPages}
                                onChange={(e) => setCrawl({ maxPages: e.target.value })}
                                style={{ marginTop: '6px', width: '100%', background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '9px', borderRadius: '8px' }}
                              />
                            </label>
                            <label title={WEB_CRAWL_HELP.maxDepth} style={{ color: '#cbd5e1', fontSize: '12px', fontWeight: 600, cursor: 'help' }}>
                              Crawl depth
                              <input
                                type="number"
                                min="0"
                                max="5"
                                value={crawl.maxDepth}
                                title={WEB_CRAWL_HELP.maxDepth}
                                onChange={(e) => setCrawl({ maxDepth: e.target.value })}
                                style={{ marginTop: '6px', width: '100%', background: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '9px', borderRadius: '8px' }}
                              />
                            </label>
                            <label title={WEB_CRAWL_HELP.restrictToPath} style={{ color: '#cbd5e1', fontSize: '12px', fontWeight: 600, display: 'flex', gap: '10px', alignItems: 'center', minHeight: '42px', padding: '0 2px', cursor: 'help' }}>
                              <input
                                type="checkbox"
                                checked={Boolean(crawl.restrictToPath)}
                                title={WEB_CRAWL_HELP.restrictToPath}
                                onChange={(e) => setCrawl({ restrictToPath: e.target.checked })}
                                style={{ width: '16px', height: '16px', margin: 0, accentColor: '#0ea5e9', cursor: 'pointer', flex: '0 0 auto' }}
                              />
                              Restrict to start path
                            </label>
                          </div>
                        );
                      })()}
                      </div>
                    ))}
                    <button
                      onClick={addKBLink}
                      style={{
                        background: 'rgba(146, 0, 220, 0.1)',
                        border: '1px dashed #9200dc',
                        color: '#fff',
                        padding: '10px 20px',
                        borderRadius: '8px',
                        cursor: 'pointer',
                        fontSize: '13px',
                        fontWeight: '600'
                      }}
                    >
                      + Add Resource
                    </button>
                  </div>

                  {agentForm.knowledgeBase.some(kb => kb.type === 'onedrive' || kb.type === 'sharepoint') && (
                    <div style={{ marginTop: '25px', padding: '20px', background: 'rgba(146, 0, 220, 0.05)', borderRadius: '12px', border: '1px solid rgba(146, 0, 220, 0.2)' }}>
                      <div style={{ display: 'flex', gap: '15px', alignItems: 'center', marginBottom: agentForm.microsoft_token ? '15px' : '0' }}>
                        <button className="ms-connect-btn" onClick={handleMSLoginForAgent} style={{ background: '#2f2f2f', color: '#fff', padding: '10px 20px', border: '1px solid #444', borderRadius: '8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px' }}>
                          <i className="fab fa-microsoft"></i> {agentForm.microsoft_token ? 'Microsoft Connected' : 'Connect to Microsoft 365'}
                        </button>
                      </div>
                      {agentForm.microsoft_token && (
                        <div className="token-display-field" style={{ position: 'relative' }}>
                          <label style={{ color: '#fff', display: 'block', marginBottom: '8px', fontSize: '13px' }}>Graph API Key (Access Token)</label>
                          <div style={{ position: 'relative' }}>
                            <input
                              type={showGraphToken ? "text" : "password"}
                              value={agentForm.microsoft_token}
                              readOnly
                              style={{ width: '100%', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)', color: '#4ade80', padding: '12px', paddingRight: '45px', borderRadius: '8px', fontSize: '12px', fontFamily: 'monospace' }}
                            />
                            <button type="button" onClick={() => setShowGraphToken(!showGraphToken)}
                              style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)', cursor: 'pointer', padding: '5px' }}
                              title={showGraphToken ? "Hide Key" : "Show Key"}>
                              <i className={`fas ${showGraphToken ? 'fa-eye-slash' : 'fa-eye'}`}></i>
                            </button>
                          </div>
                          <p style={{ color: '#aaa', fontSize: '11px', marginTop: '5px' }}>This key is automatically generated after Microsoft authentication.</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Active Plan Summary */}
              <div style={{
                margin: '32px 0 0',
                padding: '20px 24px',
                background: 'linear-gradient(135deg, rgba(146,0,220,0.12), rgba(108,92,231,0.12))',
                border: '1px solid rgba(146,0,220,0.28)',
                borderRadius: '14px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}>
                <div>
                  <div style={{ color: '#fff', fontWeight: '600', fontSize: '15px', marginBottom: '4px' }}>Active Subscription Package</div>
                  <div style={{ color: '#999', fontSize: '13px' }}>
                    Active Plan: <strong style={{ color: '#d8b4fe' }}>{activeTier.toUpperCase()}</strong> (Limit: {formatAgentLimitDisplay(subscriptionPackages, activeTier)} Custom Agents)
                  </div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <button 
                    type="button" 
                    onClick={() => {
                      setShowKnowledgebasePopup(false);
                      setShowBillingPopup(true);
                    }} 
                    style={{ 
                      background: 'rgba(108,92,231,0.2)', 
                      border: '1px solid #6c5ce7', 
                      color: '#fff', 
                      padding: '8px 16px', 
                      borderRadius: '8px', 
                      fontSize: '13px', 
                      fontWeight: '600', 
                      cursor: 'pointer' 
                    }}
                  >
                    Upgrade Plan
                  </button>
                </div>
              </div>

            </div>

            <div className="modal-footer-custom" style={{ padding: '30px 40px', borderTop: '1px solid rgba(255,255,255,0.05)', display: 'flex', gap: '15px', justifyContent: 'flex-end', alignItems: 'center' }}>
              {!agentForm.id && localStorage.getItem("agent_creation_draft") && (
                <button
                  type="button"
                  className="clear-draft-btn"
                  onClick={handleClearDraft}
                  style={{
                    background: 'rgba(239, 68, 68, 0.1)',
                    color: '#ef4444',
                    border: '1px solid rgba(239, 68, 68, 0.3)',
                    padding: '12px 30px',
                    borderRadius: '10px',
                    fontWeight: '600',
                    cursor: 'pointer',
                    marginRight: 'auto',
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
                  Clear Draft
                </button>
              )}
              <button
                className="save-agent-btn"
                onClick={handleSaveAgent}
                disabled={loading}
                style={{
                  background: '#9200dc',
                  color: '#fff',
                  padding: '12px 30px',
                  borderRadius: '10px',
                  border: 'none',
                  fontWeight: '600',
                  cursor: 'pointer',
                  boxShadow: '0 4px 14px rgba(146, 0, 220, 0.3)',
                  transition: 'all 0.3s ease'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = '#8000c4';
                  e.currentTarget.style.boxShadow = '0 6px 20px rgba(146, 0, 220, 0.55)';
                  e.currentTarget.style.transform = 'translateY(-1px)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = '#9200dc';
                  e.currentTarget.style.boxShadow = '0 4px 14px rgba(146, 0, 220, 0.3)';
                  e.currentTarget.style.transform = 'translateY(0px)';
                }}
              >
                {loading ? "Processing..." : (agentForm.id ? "Save Changes" : "Create Agent")}
              </button>
              <button
                className="cancel-agent-btn"
                onClick={toggleKnowledgebasePopup}
                disabled={loading}
                style={{ background: 'rgba(255, 255, 255, 0.05)', color: '#fff', padding: '12px 30px', borderRadius: '10px', border: '1px solid rgba(255, 255, 255, 0.2)', cursor: 'pointer' }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Billing & Subscription Modal */}
      {showBillingPopup && (
        <div className="popup-overlay-management custom-agent-modal" style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          background: 'rgba(0, 0, 0, 0.5)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'flex-start',
          zIndex: 1001,
          padding: '40px 20px',
          overflowY: 'auto'
        }}>
          <div className="popup-content-management" style={{
            background: '#0c0c0e',
            border: '1px solid rgba(146, 0, 220, 0.45)',
            borderRadius: '24px',
            width: '100%',
            maxWidth: '1280px',
            padding: '40px',
            position: 'relative',
            boxShadow: '0 0 35px rgba(146, 0, 220, 0.5), 0 25px 50px -12px rgba(0, 0, 0, 0.7)'
          }}>
            <button 
              onClick={() => setShowBillingPopup(false)} 
              style={{ 
                position: 'absolute', 
                right: '25px', 
                top: '25px', 
                background: 'none', 
                border: 'none', 
                color: '#ef4444', 
                fontSize: '20px', 
                cursor: 'pointer',
                transition: 'all 0.3s'
              }}
              onMouseEnter={(e) => e.currentTarget.style.color = '#ff6b6b'}
              onMouseLeave={(e) => e.currentTarget.style.color = '#ef4444'}
            >
              ✕
            </button>
            
            <div style={{ textAlign: 'center', marginBottom: '35px' }}>
              <h2 style={{ color: '#fff', fontSize: '28px', fontWeight: '800', margin: '0 0 10px' }}>
                Upgrade Your Workspace Plan
              </h2>
              <p style={{ color: '#aaa', fontSize: '15px', margin: 0 }}>
                Current Active Plan: <strong style={{ color: '#b026ff' }}>{(company?.subscription_tier || "FREE").toUpperCase()}</strong>
              </p>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '25px', marginBottom: '30px' }}>
              {subscriptionPackages.map((plan) => {
                const planId = String(plan.id || "").toLowerCase();
                const isActivePlan = activeTier === planId;
                const features = buildPackageFeatures(plan);
                const limitations = buildPackageLimitations(plan);
                const priceLabel = Number(plan.price_monthly_usd || 0).toFixed(
                  Number(plan.price_monthly_usd || 0) % 1 === 0 ? 0 : 2
                );

                return (
                  <div
                    key={planId}
                    style={{
                      background: plan.popular ? 'rgba(146, 0, 220, 0.03)' : 'rgba(255,255,255,0.02)',
                      border: plan.popular ? '2px solid #9200dc' : '1px solid rgba(255, 255, 255, 0.06)',
                      borderRadius: '18px',
                      padding: '30px 24px',
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'space-between',
                      position: 'relative'
                    }}
                  >
                    {plan.popular && (
                      <span style={{
                        position: 'absolute',
                        top: '-13px',
                        left: '50%',
                        transform: 'translateX(-50%)',
                        background: '#9200dc',
                        color: '#fff',
                        fontSize: '11px',
                        fontWeight: '700',
                        textTransform: 'uppercase',
                        padding: '4px 12px',
                        borderRadius: '12px',
                        letterSpacing: '1px'
                      }}>Most Popular</span>
                    )}
                    <div>
                      <h3 style={{ color: '#fff', fontSize: '18px', fontWeight: '700', margin: '0 0 8px' }}>{plan.name}</h3>
                      <p style={{ color: '#aaa', fontSize: '12px', margin: '0 0 20px', minHeight: '36px' }}>{plan.description}</p>
                      <div style={{ color: '#fff', fontSize: '28px', fontWeight: '800', margin: '0 0 20px' }}>
                        ${priceLabel}<span style={{ fontSize: '14px', color: '#777', fontWeight: '400' }}>{planId === 'free' ? ` / ${plan.agent_expiry_days || 7} days` : "/mo"}</span>
                      </div>
                      <ul style={{ paddingLeft: 0, listStyle: 'none', color: '#ccc', fontSize: '13px', lineHeight: '2', margin: '0 0 25px' }}>
                        {features.map((feature) => {
                          const isSpecial = ["Web URL", "OneDrive", "SharePoint", "Form Builder", "APIs", "MCP"].some(term => feature.includes(term));
                          return (
                            <li key={feature} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              {isSpecial ? (
                                <span style={{ color: '#b026ff', fontWeight: '900', fontSize: '14px', width: '16px', textAlign: 'center' }}>+</span>
                              ) : (
                                <span style={{ color: '#4ade80', fontWeight: 'bold', fontSize: '12px', width: '16px', textAlign: 'center' }}>✓</span>
                              )}
                              <span>{feature}</span>
                            </li>
                          );
                        })}
                        {limitations.map((limitation) => (
                          <li key={limitation} style={{ display: 'flex', alignItems: 'center', gap: '8px', opacity: 0.4 }}>
                            <span style={{ color: '#ef4444', fontWeight: 'bold', fontSize: '12px', width: '16px', textAlign: 'center' }}>✕</span>
                            <span style={{ textDecoration: 'line-through' }}>{limitation}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                     <button
                      className="upgrade-plan-btn"
                      disabled={loading || planId === 'free'}
                      onClick={() => redirectToStripe({ tier: planId, email: userInfo.email })}
                      style={{
                        width: '100%',
                        padding: '12px',
                        borderRadius: '10px',
                        border: plan.popular ? 'none' : '1px solid rgba(255,255,255,0.15)',
                        background: (planId === 'free') ? 'rgba(255,255,255,0.05)' : (plan.popular ? '#9200dc' : 'none'),
                        color: '#fff',
                        fontWeight: '600',
                        cursor: (planId === 'free') ? 'default' : 'pointer',
                        boxShadow: (planId === 'free') || !plan.popular ? 'none' : '0 4px 12px rgba(146, 0, 220, 0.35)',
                        transition: 'all 0.3s'
                      }}
                    >
                      {planId === 'free' ? (isActivePlan ? "Active Plan" : "Unavailable") : (isActivePlan ? `Renew / Extend ${plan.name}` : `Upgrade to ${plan.name}`)}
                    </button>
                  </div>
                );
              })}
            </div>
            
            <p style={{ color: '#666', fontSize: '11px', textAlign: 'center', margin: 0 }}>
              Transactions processed securely via Stripe. Plan upgrades are applied instantly to your workspace.
            </p>
          </div>
        </div>
      )}

      {/* Payment Success Modal */}
      {showPaymentSuccessModal && successPaymentDetails && (
        <div className="popup-overlay-management custom-agent-modal" style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          background: 'rgba(0, 0, 0, 0.2)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 1001,
          padding: '20px',
          overflow: 'hidden'
        }}>
          <div className="popup-content-management" style={{
            background: '#0a0a0c',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: '24px',
            width: '100%',
            maxWidth: '500px',
            padding: '40px',
            position: 'relative',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5)',
            textAlign: 'center'
          }}>
            {/* Green Success Checkmark */}
            <div style={{
              width: '80px',
              height: '80px',
              borderRadius: '50%',
              background: 'rgba(34, 197, 94, 0.1)',
              border: '2px solid rgba(34, 197, 94, 0.3)',
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              margin: '0 auto 25px',
              color: '#22c55e',
              fontSize: '36px'
            }}>
              ✓
            </div>

            <h2 style={{ color: '#fff', fontSize: '24px', fontWeight: '700', margin: '0 0 10px' }}>
              Payment Successful!
            </h2>
            <p style={{ color: '#aaa', fontSize: '15px', margin: '0 0 30px' }}>
              {successPaymentDetails.agent_name.toLowerCase().includes("subscription") || successPaymentDetails.agent_name.toLowerCase().includes("plan")
                ? "Your transaction has been processed and your workspace has been successfully upgraded."
                : "Your transaction has been processed and your agent has been successfully deployed."}
            </p>

            {/* Receipt Details Box */}
            <div style={{
              background: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid rgba(255, 255, 255, 0.05)',
              borderRadius: '16px',
              padding: '20px 25px',
              marginBottom: '35px',
              textAlign: 'left'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                <span style={{ color: '#888', fontSize: '14px' }}>
                  {successPaymentDetails.agent_name.toLowerCase().includes("subscription") || successPaymentDetails.agent_name.toLowerCase().includes("plan")
                    ? "Plan Name:"
                    : "Agent Name:"}
                </span>
                <span style={{ color: '#fff', fontSize: '14px', fontWeight: '600' }}>{successPaymentDetails.agent_name}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                <span style={{ color: '#888', fontSize: '14px' }}>Order ID:</span>
                <span style={{ color: '#9200dc', fontSize: '14px', fontWeight: '600' }}>{successPaymentDetails.order_id}</span>
              </div>
              <hr style={{ border: 'none', borderTop: '1px solid rgba(255, 255, 255, 0.05)', margin: '15px 0' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ color: '#888', fontSize: '14px' }}>Amount Paid:</span>
                <span style={{ color: '#22c55e', fontSize: '20px', fontWeight: '700' }}>
                  ${parseFloat(successPaymentDetails.amount).toFixed(2)} {(successPaymentDetails.currency || 'USD').toUpperCase()}
                </span>
              </div>
            </div>

            {/* Close Button */}
            <button
              onClick={() => {
                const isSub = successPaymentDetails?.agent_name?.toLowerCase().includes("subscription") || 
                              successPaymentDetails?.agent_name?.toLowerCase().includes("plan");
                setShowPaymentSuccessModal(false);
                setSuccessPaymentDetails(null);
                if (isSub) {
                  window.location.reload();
                }
              }}
              style={{
                background: '#9200dc',
                color: '#fff',
                padding: '14px 40px',
                borderRadius: '12px',
                border: 'none',
                fontWeight: '600',
                fontSize: '15px',
                cursor: 'pointer',
                width: '100%',
                boxShadow: '0 8px 16px rgba(146, 0, 220, 0.25)',
                transition: 'all 0.3s'
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = '#8000c4'}
              onMouseLeave={(e) => e.currentTarget.style.background = '#9200dc'}
            >
              {successPaymentDetails.agent_name.toLowerCase().includes("subscription") || successPaymentDetails.agent_name.toLowerCase().includes("plan")
                ? "Close & View Dashboard"
                : "Close & View Agent"}
            </button>
          </div>
        </div>
      )}
      {/* Onboarding Welcome Modal */}
      {showOnboarding && (
        <div className="popup-overlay-management" style={{ zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div className="popup-content-management onboarding-modal-container" style={{
            maxWidth: '960px',
            width: '90%',
            background: '#0d0b14',
            border: '1px solid rgba(146, 0, 220, 0.4)',
            borderRadius: '24px',
            padding: '0',
            overflow: 'hidden',
            boxShadow: '0 0 45px rgba(146, 0, 220, 0.25), 0 25px 60px rgba(0,0,0,0.85)',
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'stretch',
            position: 'relative',
          }}>
            {/* Close Button */}
            <button
              onClick={closeOnboarding}
              title="Close"
              style={{
                position: 'absolute',
                top: '14px',
                right: '16px',
                zIndex: 10,
                background: 'none',
                border: 'none',
                color: '#f87171',
                fontSize: '20px',
                cursor: 'pointer',
                lineHeight: 1,
                padding: '4px 8px',
                transition: 'color 0.2s ease',
              }}
              onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; }}
              onMouseLeave={e => { e.currentTarget.style.color = '#f87171'; }}
            >
              ✕
            </button>

            {/* Left Column: Welcome, Features, Options */}
            <div style={{
              flex: '1.2',
              padding: '40px',
              textAlign: 'left',
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center'
            }}>
              <h2 style={{
                fontFamily: "'Sora', sans-serif",
                color: '#fff',
                fontSize: '28px',
                fontWeight: '800',
                marginBottom: '12px',
                background: 'linear-gradient(135deg, #c084fc, #a855f7)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
              }}>
                Welcome to Agent Store
              </h2>
              <p style={{
                color: 'rgba(255, 255, 255, 0.7)',
                fontSize: '14px',
                lineHeight: '1.5',
                marginBottom: '24px'
              }}>
                Your workspace has been created successfully. Configure your subscription plan now to begin deploying custom AI assistants.
              </p>

              {/* Features List */}
              <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '12px',
                marginBottom: '28px',
                background: 'rgba(255, 255, 255, 0.02)',
                border: '1px solid rgba(255, 255, 255, 0.04)',
                borderRadius: '16px',
                padding: '20px 24px'
              }}>
                {[
                  { title: "Deploy Custom AI Agents", desc: "Create tailored chatbots trained on your business data" },
                  { title: "OneDrive, SharePoint & Web URL KB", desc: "Connect cloud storage or scrape public web URLs as knowledge source" },
                  { title: "Form Builder", desc: "Build forms that gather info through voice or text chat" },
                  { title: "APIs", desc: "Connect external REST endpoints and trigger webhooks" },
                  { title: "MCP (Model Context Protocol)", desc: "Integrate Model Context Protocol servers dynamically" }
                ].map((feat, index) => (
                  <div key={index} style={{ display: 'flex', alignItems: 'flex-start', gap: '12px' }}>
                    <span style={{
                      color: '#a855f7',
                      fontSize: '16px',
                      fontWeight: 'bold',
                      background: 'rgba(168, 85, 247, 0.1)',
                      border: '1px solid rgba(168, 85, 247, 0.3)',
                      borderRadius: '50%',
                      width: '24px',
                      height: '24px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0
                    }}>✓</span>
                    <div>
                      <strong style={{ color: '#fff', fontSize: '13.5px', display: 'block' }}>{feat.title}</strong>
                      <span style={{ color: 'rgba(255, 255, 255, 0.5)', fontSize: '11.5px' }}>{feat.desc}</span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Action Choices */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                {/* Option 1: Start 7-Day Free Trial */}
                <div 
                  onClick={() => handleOnboardingOption("trial")}
                  style={{
                    background: 'rgba(146, 0, 220, 0.05)',
                    border: '1px solid rgba(146, 0, 220, 0.35)',
                    borderRadius: '14px',
                    padding: '16px 20px',
                    cursor: 'pointer',
                    transition: 'all 0.3s ease',
                  }}
                  className="onboarding-option-card"
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <strong style={{ color: '#fff', fontSize: '14px' }}>Option 1: Start {trialDurationDays}-Day Free Trial</strong>
                    <span style={{
                      background: '#9200dc',
                      color: '#fff',
                      fontSize: '9px',
                      fontWeight: '700',
                      textTransform: 'uppercase',
                      padding: '2px 8px',
                      borderRadius: '6px'
                    }}>Recommended</span>
                  </div>
                  <p style={{ color: 'rgba(255, 255, 255, 0.55)', fontSize: '11.5px', margin: 0, lineHeight: '1.3' }}>
                    Activate your {trialDurationDays}-day Free Trial immediately. No credit card required.
                  </p>
                </div>

                {/* Option 2: Choose a Premium Plan */}
                <div 
                  onClick={() => handleOnboardingOption("upgrade")}
                  style={{
                    background: 'rgba(255, 255, 255, 0.02)',
                    border: '1px solid rgba(255, 255, 255, 0.08)',
                    borderRadius: '14px',
                    padding: '16px 20px',
                    cursor: 'pointer',
                    transition: 'all 0.3s ease',
                  }}
                  className="onboarding-option-card"
                >
                  <strong style={{ color: '#fff', fontSize: '14px', display: 'block', marginBottom: '4px' }}>Option 2: Purchase Premium Subscription</strong>
                  <p style={{ color: 'rgba(255, 255, 255, 0.55)', fontSize: '11.5px', margin: 0, lineHeight: '1.3' }}>
                    Select from our Starter, Business, or Enterprise tiers for full capabilities.
                  </p>
                </div>
              </div>
            </div>

            {/* Right Column: Chatbot Illustration */}
            <div style={{
              flex: '0.8',
              background: 'linear-gradient(135deg, #1d112d, #0c0818)',
              borderLeft: '1px solid rgba(146, 0, 220, 0.15)',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '40px',
              position: 'relative'
            }}
            className="onboarding-right-col"
            >
              <div style={{
                position: 'absolute',
                width: '180px',
                height: '180px',
                background: 'rgba(146, 0, 220, 0.2)',
                borderRadius: '50%',
                filter: 'blur(40px)',
                zIndex: 1
              }} />
              
              <img 
                src="/aisleaiagents.png" 
                alt="AI Chatbot Assistant" 
                style={{
                  width: '100%',
                  maxWidth: '260px',
                  height: 'auto',
                  borderRadius: '16px',
                  boxShadow: '0 15px 35px rgba(0, 0, 0, 0.4)',
                  zIndex: 2,
                  animation: 'floatAnimation 4s ease-in-out infinite'
                }}
              />
              
              <div style={{ zIndex: 2, marginTop: '30px', textAlign: 'center' }}>
                <h3 style={{ color: '#fff', fontSize: '16px', fontWeight: '700', margin: '0 0 6px' }}>Empower Your Workflow</h3>
                <p style={{ color: 'rgba(255,255,255,0.4)', fontSize: '12px', margin: 0, maxWidth: '220px', lineHeight: '1.4' }}>
                  Deploy custom AI agents built to handle customer support, sync KBs, and run operations.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      <Footer />

      <style>{`
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }

        @keyframes floatAnimation {
          0% { transform: translateY(0px); }
          50% { transform: translateY(-10px); }
          100% { transform: translateY(0px); }
        }

        @keyframes pulse-red-border {
          0% { border-color: rgba(239, 68, 68, 0.4); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.2); }
          50% { border-color: rgba(239, 68, 68, 1); box-shadow: 0 0 10px 4px rgba(239, 68, 68, 0.45); }
          100% { border-color: rgba(239, 68, 68, 0.4); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.2); }
        }

        .tool-row-error-blink {
          animation: pulse-red-border 1.5s infinite ease-in-out !important;
          border: 1px solid #ef4444 !important;
        }

        .onboarding-option-card:hover {
          background: rgba(146, 0, 220, 0.08) !important;
          border-color: #9200dc !important;
          transform: translateY(-2px);
          box-shadow: 0 4px 15px rgba(146, 0, 220, 0.15);
        }

        .upgrade-plan-btn {
          transition: all 0.3s ease !important;
        }

        .upgrade-plan-btn:hover:not(:disabled) {
          background: #a855f7 !important;
          border-color: #a855f7 !important;
          box-shadow: 0 0 20px rgba(168, 85, 247, 0.75) !important;
          transform: translateY(-2px) !important;
          color: #fff !important;
        }

        @media (max-width: 768px) {
          .onboarding-modal-container {
            flex-direction: column !important;
            max-height: 90vh;
            overflow-y: auto !important;
          }
          .onboarding-right-col {
            display: none !important;
          }
        }
      `}</style>

    </div>
  );
};

const AgentCard = ({ agentName, iconSrc, description, toolLabels = [], showToolsToggle = true, onClick, isExpired }) => {
  const [showTools, setShowTools] = useState(false);

  const handleToolsClick = (event) => {
    event.stopPropagation();
    setShowTools((current) => !current);
  };

  return (
    <div
      className={`agent-card ${showTools ? "agent-card-expanded" : ""}`}
      onClick={onClick}
      style={isExpired ? { filter: "grayscale(100%)", opacity: 0.6 } : {}}
    >
      <img
        src={iconSrc}
        alt={`${agentName} Icon`}
        className="agent-icon"
      />
      <div className="agent-card-body">
        <div className="agent-name" style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <span>{agentName}</span>
          {isExpired && (
            <span style={{
              background: "rgba(239, 68, 68, 0.15)",
              color: "#ef4444",
              border: "1px solid rgba(239, 68, 68, 0.3)",
              padding: "2px 8px",
              borderRadius: "4px",
              fontSize: "10px",
              fontWeight: "700",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              display: "inline-block"
            }}>
              Expired
            </span>
          )}
        </div>
        {showToolsToggle && (
          <>
            <div className="agent-tools-toggle-row">
              <button
                type="button"
                className="agent-tools-toggle"
                onClick={handleToolsClick}
                aria-expanded={showTools}
              >
                {showTools ? "Hide Tools" : "View Tools"}
              </button>
            </div>
            <div className={`agent-tools-panel ${showTools ? "is-open" : ""}`}>
              {toolLabels.length > 0 ? (
                <div className="agent-tool-badges" aria-label="Configured tools">
                  {toolLabels.map((label) => (
                    <span className="agent-tool-badge" key={label}>
                      {label}
                    </span>
                  ))}
                </div>
              ) : (
                <div className="agent-no-tools">No tools configured</div>
              )}
            </div>
          </>
        )}
        {description && (
          <p className="agent-card-desc">{description}</p>
        )}
      </div>
    </div>
  );
};

export default Dashboard;
