export const PACKAGE_ORDER = ["free", "starter", "business", "enterprise"];

export const isUnlimited = (value) =>
  value === null || value === undefined || value === "" || value === -1;

export const formatLimit = (value, { unit = "", unlimitedLabel = "Unlimited" } = {}) => {
  if (isUnlimited(value)) return unlimitedLabel;
  const numeric = Number(value);
  if (Number.isNaN(numeric)) return unlimitedLabel;
  const formatted = numeric.toLocaleString();
  return unit ? `${formatted} ${unit}` : formatted;
};

export const getPackageByTier = (packages, tier = "free") => {
  const list = Array.isArray(packages) ? packages : [];
  const key = String(tier || "free").toLowerCase();
  return list.find((pkg) => String(pkg.id || "").toLowerCase() === key) || null;
};

export const formatAgentLimitDisplay = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  if (!pkg || isUnlimited(pkg.agent_limit)) return "Unlimited";
  return String(pkg.agent_limit ?? 1);
};

export const limitInputValue = (value) => {
  if (isUnlimited(value)) return "";
  return value ?? "";
};

export const parseLimitInput = (value) => {
  const text = String(value ?? "").trim();
  if (!text || text.toLowerCase() === "unlimited") return null;
  const numeric = Number(text);
  return Number.isNaN(numeric) ? null : numeric;
};

export const getTierAgentLimit = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  if (!pkg) return 1;
  if (isUnlimited(pkg.agent_limit)) return Number.MAX_SAFE_INTEGER;
  return Number(pkg.agent_limit) || 1;
};

export const tierAllowsFormBuilder = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  return Boolean(pkg?.allow_form_builder);
};

export const tierAllowsEndpointTools = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  return Boolean(pkg?.allow_endpoint_tools);
};

export const tierAllowsOneDrive = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  return Boolean(pkg?.allow_onedrive_kb);
};

export const tierAllowsSharePoint = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  return Boolean(pkg?.allow_sharepoint_kb);
};

export const tierAllowsDocuments = (packages, tier = "free") => {
  const pkg = getPackageByTier(packages, tier);
  return Boolean(pkg?.allow_documents_kb);
};

export const buildPackageFeatures = (pkg) => {
  if (Array.isArray(pkg?.features) && pkg.features.length) return pkg.features;
  if (!pkg) return [];

  const lines = [];
  if (isUnlimited(pkg.agent_limit)) {
    lines.push("Unlimited Agents");
  } else {
    lines.push(`${pkg.agent_limit} Agent${Number(pkg.agent_limit) === 1 ? "" : "s"}`);
  }

  if (pkg.allow_website_kb) lines.push("Web URL Knowledge Base");
  if (pkg.allow_onedrive_kb) lines.push("OneDrive Knowledge Base");
  if (pkg.allow_sharepoint_kb) lines.push("SharePoint Knowledge Base");

  if (pkg.allow_form_builder) lines.push("Form Builder");
  if (pkg.allow_endpoint_tools) lines.push("APIs");
  if (pkg.allow_endpoint_tools) lines.push("Database Tool");
  if (pkg.allow_mcp) lines.push("MCP");
  lines.push(`${formatLimit(pkg.monthly_tokens)} monthly tokens`);

  if (isUnlimited(pkg.knowledge_bases_limit)) {
    lines.push("Unlimited Knowledge Bases");
  } else if (pkg.knowledge_bases_limit != null) {
    lines.push(`Up to ${pkg.knowledge_bases_limit} Knowledge Base${Number(pkg.knowledge_bases_limit) === 1 ? "" : "s"}`);
  }

  if (isUnlimited(pkg.websites_limit)) {
    lines.push("Unlimited Websites");
  }

  return lines;
};

export const buildPackageLimitations = (pkg) => {
  if (Array.isArray(pkg?.limitations) && pkg.limitations.length) return pkg.limitations;
  if (!pkg) return [];

  const lines = [];
  if (!pkg.allow_form_builder) lines.push("No Form Builder");
  if (!pkg.allow_endpoint_tools) lines.push("No APIs");
  if (!pkg.allow_endpoint_tools) lines.push("No Database Tool");
  if (!pkg.allow_mcp) lines.push("No MCP");
  if (!pkg.allow_onedrive_kb) lines.push("No OneDrive Knowledge Base");
  if (!pkg.allow_sharepoint_kb) lines.push("No SharePoint Knowledge Base");
  return lines;
};
