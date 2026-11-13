import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  FiAlertTriangle,
  FiChevronDown,
  FiChevronUp,
  FiCheckCircle,
  FiDatabase,
  FiEdit3,
  FiMessageSquare,
  FiMic,
  FiMoreVertical,
  FiPlus,
  FiSearch,
  FiSlash,
  FiTrash2,
  FiVolume2,
  FiVolumeX,
  FiX,
} from "react-icons/fi";
import { IoSend } from "react-icons/io5";
import AgentChatShell from "./AgentChatShell";
import { URL } from "../urls";
import { getToken } from "../auth/authClient";
import { useAppDialog } from "./AppDialog";

const apiErrorMessage = (data, status) => data?.message || data?.error || `HTTP ${status}`;

const renderInlineMarkdown = (text) => {
  const parts = String(text || "").split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={index}>{part}</React.Fragment>;
  });
};

const normalizeMarkdownText = (text) => {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/([^\n])\s+(-\s+\*\*)/g, "$1\n$2")
    .replace(/([^\n])\s+(\d+\.\s+\*\*)/g, "$1\n$2");
};

const isAssignedResourceRefusal = (text) => {
  const normalized = String(text || "").toLowerCase();
  return (
    normalized.includes("i don't have that information in this agent's assigned resources") ||
    normalized.includes("outside this agent's assigned resources")
  );
};

const sourceLabel = (source, index) => (
  source?.document_title || source?.source_ref || source?.document_id || `chunk ${source?.chunk_index ?? index}`
);

const uniqueSourceLabels = (sources = []) => {
  const seen = new Set();
  return sources
    .map(sourceLabel)
    .map(label => String(label || "").trim())
    .filter(label => {
      const key = label.toLowerCase();
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
};

const parseAgentConfiguration = (agent) => {
  const configuration = agent?.configuration;
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

const configuredDatabaseProfiles = (agent) => {
  const configuration = parseAgentConfiguration(agent);
  const dbConfig = (
    configuration.database ||
    configuration.database_config ||
    configuration.databaseConfig ||
    configuration.structured_database ||
    configuration.structuredDatabase ||
    {}
  );
  const rawProfiles = Array.isArray(dbConfig)
    ? dbConfig
    : (
      dbConfig.profiles ||
      dbConfig.connections ||
      dbConfig.databases ||
      dbConfig.profile ||
      []
    );
  const profiles = Array.isArray(rawProfiles) ? rawProfiles : (rawProfiles ? [rawProfiles] : []);
  return profiles.slice(0, 3).map((profile, index) => ({
    index,
    name: profile?.name || profile?.label || profile?.database || `Database Profile ${index + 1}`,
    purpose: profile?.purpose || profile?.description || profile?.profilePurpose || "",
    engine: profile?.engine || profile?.type || "database",
  }));
};

const controlTypeForField = (field) => {
  return String(field?.inputType || field?.type || "text").toLowerCase();
};

const inputTypeForField = (field) => {
  const normalized = String(field?.dataType || field?.inputType || field?.type || "text").toLowerCase();
  if (normalized.includes("email")) return "email";
  if (normalized.includes("number")) return "number";
  if (normalized.includes("date")) return "date";
  if (normalized.includes("phone") || normalized.includes("tel")) return "tel";
  return "text";
};

const splitMarkdownTableRow = (line) => {
  const trimmed = String(line || "").trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
  const cells = [];
  let current = "";
  let escaped = false;
  for (let index = 1; index < trimmed.length - 1; index += 1) {
    const char = trimmed[index];
    if (escaped) {
      current += char;
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
};

const isMarkdownTableSeparator = (line) => {
  const cells = splitMarkdownTableRow(line);
  return Boolean(cells?.length) && cells.every(cell => /^:?-{3,}:?$/.test(cell.trim()));
};

const FormattedMessage = ({ text }) => {
  const lines = normalizeMarkdownText(text).split("\n");
  const blocks = [];
  let listItems = [];
  let orderedItems = [];

  const flushLists = () => {
    if (listItems.length) {
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="agent-message-list">
          {listItems.map((item, index) => (
            <li key={index}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>
      );
      listItems = [];
    }
    if (orderedItems.length) {
      blocks.push(
        <ol key={`ol-${blocks.length}`} className="agent-message-list">
          {orderedItems.map((item, index) => (
            <li key={index}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>
      );
      orderedItems = [];
    }
  };

  for (let index = 0; index < lines.length; index += 1) {
    const tableHeader = splitMarkdownTableRow(lines[index]);
    if (tableHeader && isMarkdownTableSeparator(lines[index + 1] || "")) {
      flushLists();
      const tableRows = [];
      let rowIndex = index + 2;
      while (rowIndex < lines.length) {
        const row = splitMarkdownTableRow(lines[rowIndex]);
        if (!row) break;
        tableRows.push(row);
        rowIndex += 1;
      }
      blocks.push(
        <div key={`table-${index}`} className="agent-message-table-wrap">
          <table className="agent-message-table">
            <thead>
              <tr>
                {tableHeader.map((cell, cellIndex) => (
                  <th key={cellIndex}>{renderInlineMarkdown(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((row, bodyRowIndex) => (
                <tr key={bodyRowIndex}>
                  {tableHeader.map((_, cellIndex) => (
                    <td key={cellIndex}>{renderInlineMarkdown(row[cellIndex] || "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      index = rowIndex - 1;
      continue;
    }

    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      flushLists();
      continue;
    }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushLists();
      blocks.push(
        <div key={`h-${index}`} className="agent-message-heading">
          {renderInlineMarkdown(heading[2])}
        </div>
      );
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      orderedItems = [];
      listItems.push(bullet[1]);
      continue;
    }

    const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      listItems = [];
      orderedItems.push(ordered[1]);
      continue;
    }

    flushLists();
    blocks.push(
      <p key={`p-${index}`} className="agent-message-paragraph">
        {renderInlineMarkdown(trimmed)}
      </p>
    );
  }

  flushLists();
  return <div className="agent-formatted-message">{blocks}</div>;
};

const submittedFormIdsFromMessages = (messageList = []) => {
  const submittedIds = new Set();
  messageList.forEach((message) => {
    if (message.type !== "tool_result" || !message.text) return;
    try {
      const payload = JSON.parse(message.text);
      const formId = payload.formId || payload.form_id;
      if (formId && payload.status === "success") {
        submittedIds.add(String(formId));
      }
    } catch {
      // Ignore non-JSON tool result messages.
    }
  });
  return submittedIds;
};

const markSubmittedForms = (messageList = []) => {
  const submittedIds = submittedFormIdsFromMessages(messageList);
  if (!submittedIds.size) return messageList;
  return messageList.map((message) => {
    const formId = message.form?.id;
    if (message.type === "bot" && formId && submittedIds.has(String(formId))) {
      return { ...message, formSubmitted: true };
    }
    return message;
  });
};

const databaseConfirmationFromMessage = (message) => {
  const results = Array.isArray(message?.apiResults)
    ? message.apiResults
    : (Array.isArray(message?.api_results) ? message.api_results : []);
  const structuredConfirmation = results.find((result) =>
    result?.type === "database" && result?.status === "pending_user_confirmation"
  );
  return structuredConfirmation || legacyDatabaseConfirmationFromMessage(message);
};

const databaseConfirmationId = (confirmation) =>
  String(confirmation?.approval_id || confirmation?.approvalId || "").trim();

const databaseConfirmationCommand = (confirmation, action) => {
  const id = databaseConfirmationId(confirmation);
  return id ? `${action === "cancel" ? "Cancel" : "Run"} database write #${id}` : "";
};

const escapeRegExp = (value) => String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const databaseConfirmationStatusFromMessages = (messageList, messageIndex, confirmationId) => {
  if (!confirmationId) return false;
  const escapedId = escapeRegExp(confirmationId);
  const patterns = [
    ["completed", new RegExp(`database write #${escapedId} completed`, "i")],
    ["cancelled", new RegExp(`cancelled database write #${escapedId}`, "i")],
    ["failed", new RegExp(`write #${escapedId}.*failed`, "i")],
  ];
  for (const message of messageList.slice(messageIndex + 1)) {
    if (message?.type !== "bot") continue;
    const text = String(message.text || "");
    const match = patterns.find(([, pattern]) => pattern.test(text));
    if (match) return match[0];
  }
  return "";
};

const legacyDatabaseConfirmationFromMessage = (message) => {
  const text = String(message?.text || "");
  const quickReplies = Array.isArray(message?.quickReplies) ? message.quickReplies : [];
  const combined = [text, ...quickReplies].join("\n");
  const idMatch = combined.match(/\b(?:run|cancel)\s+(?:database\s+write|query)\s+#?(\d+)\b/i);
  if (!idMatch || !/please confirm|estimated affected rows|database profile|sql:/i.test(text)) {
    return null;
  }

  const operation =
    text.match(/\b(INSERT|UPDATE|DELETE)\s+query\b/i)?.[1]?.toLowerCase() ||
    text.match(/```sql\s*(INSERT|UPDATE|DELETE)\b/i)?.[1]?.toLowerCase() ||
    "write";
  const sqlBlock = text.match(/```(?:sql)?\s*([\s\S]*?)```/i)?.[1]?.trim() || "";
  const sql = sqlBlock.replace(/^sql\s+/i, "").trim();
  const profileName = text.match(/Database profile:\s*([^\n]+)/i)?.[1]?.trim() || "Database";
  const estimatedRows = text.match(/Estimated affected rows:\s*([^\n]+)/i)?.[1]?.trim();
  const maxRows = text.match(/affected-row limit:\s*([^\n]+)/i)?.[1]?.trim();

  return {
    type: "database",
    ok: true,
    status: "pending_user_confirmation",
    approval_id: idMatch[1],
    profile_name: profileName,
    engine: "",
    operation,
    risk_label: operation === "delete" ? "delete data" : "change data",
    request: { sql },
    response: {
      estimated_affected_rows: estimatedRows,
      max_affected_rows: maxRows,
    },
  };
};

const databaseOperationDetails = (operation) => {
  const normalized = String(operation || "").toLowerCase();
  if (normalized === "insert") {
    return {
      action: "add data",
      description: "The agent wants to add new data to the database.",
      tone: "insert",
    };
  }
  if (normalized === "update") {
    return {
      action: "modify data",
      description: "The agent wants to change existing data in the database.",
      tone: "update",
    };
  }
  if (normalized === "delete") {
    return {
      action: "delete data",
      description: "The agent wants to delete data from the database.",
      tone: "delete",
    };
  }
  return {
    action: "change data",
    description: "The agent wants to run a database query that can change data.",
    tone: "write",
  };
};

const formatDatabaseRowCount = (value) => {
  if (value === null || value === undefined || value === "") return "Unknown";
  const count = Number(value);
  if (!Number.isFinite(count)) return String(value);
  return `${count.toLocaleString()} ${count === 1 ? "row" : "rows"}`;
};

const DatabaseConfirmationCard = ({ confirmation, disabled, status = "pending", onRun, onCancel }) => {
  const [showSql, setShowSql] = useState(false);
  const response = confirmation?.response || {};
  const request = confirmation?.request || {};
  const operation = String(confirmation?.operation || "write").toUpperCase();
  const profileName = confirmation?.profile_name || confirmation?.profileName || "Database";
  const engine = String(confirmation?.engine || "").replace(/_/g, " ");
  const sql = request.sql || confirmation?.sql || "";
  const estimatedRows = response.estimated_affected_rows ?? response.estimatedAffectedRows;
  const maxRows = response.max_affected_rows ?? response.maxAffectedRows;
  const previewError = response.preview_error || response.previewError;
  const riskLabel = confirmation?.risk_label || confirmation?.riskLabel || "change data";
  const isPending = status === "pending";
  const actionDisabled = disabled || !isPending;
  const operationDetails = databaseOperationDetails(operation);
  const primaryActionLabel = {
    cancelled: "Cancelled",
    completed: "Approved",
    failed: "Failed",
    submitted: "Submitted",
  }[status] || "Approve and Run";

  return (
    <div className={`agent-db-confirmation-card ${operationDetails.tone}`}>
      <div className="agent-db-confirmation-header">
        <div className="agent-db-confirmation-heading">
          <div className="agent-db-confirmation-icon">
            <FiDatabase />
          </div>
          <div>
            <div className="agent-db-confirmation-kicker">Approval Required</div>
            <div className="agent-db-confirmation-title">Review Database Change</div>
          </div>
        </div>
        <span className="agent-db-confirmation-badge">{operation}</span>
      </div>

      <div className="agent-db-confirmation-summary">
        <FiAlertTriangle />
        <div>
          <strong>{operationDetails.description}</strong>
          <span>This can {riskLabel}. Nothing has been run yet.</span>
        </div>
      </div>

      <div className="agent-db-confirmation-grid">
        <div>
          <span>Database Profile</span>
          <strong>{profileName}</strong>
        </div>
        {engine && (
          <div>
            <span>Engine</span>
            <strong>{engine}</strong>
          </div>
        )}
        <div>
          <span>Expected Impact</span>
          <strong>{formatDatabaseRowCount(estimatedRows)}</strong>
        </div>
        <div>
          <span>Safety Limit</span>
          <strong>{formatDatabaseRowCount(maxRows)}</strong>
        </div>
      </div>

      {previewError && <div className="agent-db-confirmation-note">Preview Note: {previewError}</div>}
      {sql && (
        <div className="agent-db-confirmation-sql-wrap">
          <button
            type="button"
            className="agent-db-confirmation-sql-toggle"
            onClick={() => setShowSql((current) => !current)}
          >
            <span>{showSql ? "Hide SQL" : "View SQL"}</span>
            {showSql ? <FiChevronUp /> : <FiChevronDown />}
          </button>
          {showSql && <pre className="agent-db-confirmation-sql">{sql}</pre>}
        </div>
      )}

      <div className="agent-db-confirmation-actions">
        <button type="button" className="agent-db-confirmation-run" onClick={onRun} disabled={actionDisabled}>
          <FiCheckCircle />
          <span>{primaryActionLabel}</span>
        </button>
        <button type="button" className="agent-db-confirmation-cancel" onClick={onCancel} disabled={actionDisabled}>
          <FiSlash />
          <span>{isPending ? "Do Not Run" : "Unavailable"}</span>
        </button>
      </div>
    </div>
  );
};

const deriveChatTitle = (text) => {
  const normalized = String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (!normalized || normalized === "__tool_result__") return "New chat";
  if (["hi", "hello", "hey", "good morning", "good afternoon", "good evening"].includes(normalized)) {
    return "Casual Greeting";
  }

  const phraseTitles = [
    ["problem statement", "Problem Statement"],
    ["research gap", "Research Gap"],
    ["literature review", "Literature Review"],
    ["methodology", "Methodology"],
    ["conclusion", "Conclusion"],
    ["summary", "Summary"],
    ["summarize", "Summary"],
    ["email", "Email Draft"],
    ["proposal", "Proposal"],
    ["presentation", "Presentation"],
    ["report", "Report Review"],
    ["code", "Code Help"],
    ["error", "Error Troubleshooting"],
    ["bug", "Bug Troubleshooting"],
  ];
  const matchedTitle = phraseTitles.find(([phrase]) => normalized.includes(phrase));
  if (matchedTitle) return matchedTitle[1];

  const stopWords = new Set([
    "a", "an", "and", "are", "about", "can", "could", "do", "does", "for", "from",
    "give", "help", "how", "i", "in", "is", "it", "me", "of", "on", "please",
    "tell", "that", "the", "this", "to", "what", "when", "where", "which", "why",
    "with", "write", "explain", "describe", "show", "need", "want",
  ]);
  const words = normalized
    .split(" ")
    .map((word) => word.replace(/^[.,?!:;()[\]{}"']+|[.,?!:;()[\]{}"']+$/g, ""))
    .filter((word) => word && !stopWords.has(word))
    .slice(0, 4);

  return words.length ? words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ") : "New Chat";
};

const sortSessionsByUpdatedAt = (sessions = []) => {
  return [...sessions].sort((a, b) => {
    const bTime = new Date(b.updated_at || b.created_at || 0).getTime();
    const aTime = new Date(a.updated_at || a.created_at || 0).getTime();
    return bTime - aTime;
  });
};

const InlineAgentForm = ({ form, onSubmit, onDismiss, initiallySubmitted = false }) => {
  const initialValues = {};
  (form?.fields || []).forEach((field) => {
    initialValues[field.name] = controlTypeForField(field).includes("checkbox") ? false : "";
  });
  const [values, setValues] = useState(initialValues);
  const [submitted, setSubmitted] = useState(Boolean(initiallySubmitted));
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const updateValue = (field, value) => {
    setValues((prev) => ({ ...prev, [field.name]: value }));
  };

  const submitForm = async (event) => {
    event.preventDefault();
    if (submitted || isSubmitting) return;
    setError("");
    setIsSubmitting(true);
    try {
      await onSubmit(form, values);
      setSubmitted(true);
    } catch (submitError) {
      setError(submitError.message || "Unable to save this submission.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form className="agent-inline-form" onSubmit={submitForm}>
      <div className="agent-inline-form-title">{form?.title || "Request Form"}</div>
      {form?.description && <div className="agent-inline-form-description">{form.description}</div>}

      {(form?.fields || []).map((field) => {
        const controlType = controlTypeForField(field);
        const isTextarea = controlType.includes("textarea");
        const isCheckbox = controlType.includes("checkbox");
        const isDropdown = controlType.includes("dropdown") || controlType.includes("select");
        const isRadio = controlType.includes("radio");
        const options = Array.isArray(field.options) ? field.options : [];
        return (
          <label key={field.name} className="agent-inline-form-field">
            <span>
              {field.label}
              {field.required ? <strong> *</strong> : null}
            </span>
            {isDropdown ? (
              <select
                value={values[field.name] || ""}
                onChange={(event) => updateValue(field, event.target.value)}
                required={field.required}
                disabled={submitted || isSubmitting}
              >
                <option value="">Select...</option>
                {options.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            ) : isRadio ? (
              <div className="agent-inline-radio-group">
                {options.map((option) => (
                  <label key={option} className="agent-inline-choice">
                    <input
                      type="radio"
                      name={field.name}
                      value={option}
                      checked={values[field.name] === option}
                      onChange={(event) => updateValue(field, event.target.value)}
                      required={field.required}
                      disabled={submitted || isSubmitting}
                    />
                    <span>{option}</span>
                  </label>
                ))}
              </div>
            ) : isTextarea ? (
              <textarea
                value={values[field.name] || ""}
                onChange={(event) => updateValue(field, event.target.value)}
                placeholder={field.placeholder || ""}
                required={field.required}
                disabled={submitted || isSubmitting}
                rows={3}
              />
            ) : isCheckbox ? (
              <input
                type="checkbox"
                checked={!!values[field.name]}
                onChange={(event) => updateValue(field, event.target.checked)}
                required={field.required}
                disabled={submitted || isSubmitting}
              />
            ) : (
              <input
                type={inputTypeForField(field)}
                value={values[field.name] || ""}
                onChange={(event) => updateValue(field, event.target.value)}
                placeholder={field.placeholder || ""}
                required={field.required}
                disabled={submitted || isSubmitting}
              />
            )}
          </label>
        );
      })}

      {error && <div className="agent-inline-form-error">{error}</div>}
      <div className="agent-inline-form-actions">
        <button type="submit" className="agent-inline-form-submit" disabled={submitted || isSubmitting}>
          {submitted ? "Submitted" : isSubmitting ? "Saving..." : "Submit"}
        </button>
        {!submitted && (
          <button type="button" className="agent-inline-form-dismiss" onClick={() => onDismiss(form)} disabled={isSubmitting}>
            Dismiss
          </button>
        )}
      </div>
    </form>
  );
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

const CustomAgentChat = ({ userRole }) => {
  const { agentId } = useParams();
  const { showConfirm, showAlert } = useAppDialog();
  const [agent, setAgent] = useState(null);
  const [messages, setMessages] = useState([]);
  const [inputMessage, setInputMessage] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isHistoryLoading, setIsHistoryLoading] = useState(false);
  const [chatSessions, setChatSessions] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [chatSearch, setChatSearch] = useState("");
  const [openMenuId, setOpenMenuId] = useState(null);
  const [renamingChatId, setRenamingChatId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [shownFormIds, setShownFormIds] = useState([]);
  const [dismissedFormIds, setDismissedFormIds] = useState([]);
  const [handledDatabaseConfirmations, setHandledDatabaseConfirmations] = useState([]);
  const [selectedDatabaseProfile, setSelectedDatabaseProfile] = useState("auto");
  const selectedDatabaseProfileRef = useRef("auto");
  const databaseProfileSelectRef = useRef(null);
  const [isListening, setIsListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState(() => {
    try {
      return localStorage.getItem("agentChat.autoSpeak") === "true";
    } catch {
      return false;
    }
  });
  const recognitionRef = useRef(null);

  const updateSelectedDatabaseProfile = (value) => {
    const nextValue = value === "auto" || value === null || value === undefined || value === ""
      ? "auto"
      : String(value);
    selectedDatabaseProfileRef.current = nextValue;
    setSelectedDatabaseProfile(nextValue);
  };

  useEffect(() => {
    const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionCtor) {
      setVoiceSupported(false);
      return undefined;
    }
    const recognition = new SpeechRecognitionCtor();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognitionRef.current = recognition;
    setVoiceSupported(true);

    return () => {
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      try {
        recognition.stop();
      } catch {
        // ignore - recognition may already be stopped
      }
      recognitionRef.current = null;
      window.speechSynthesis?.cancel();
    };
  }, []);

  const toggleListening = () => {
    const recognition = recognitionRef.current;
    if (!recognition) return;
    if (isListening) {
      recognition.stop();
      return;
    }

    window.speechSynthesis?.cancel();
    let finalTranscript = "";

    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const transcript = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += transcript;
        } else {
          interim += transcript;
        }
      }
      setInputMessage((finalTranscript + interim).trim());
    };

    recognition.onerror = (event) => {
      setIsListening(false);
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        showAlert("Microphone access was denied. Please allow microphone permission to use voice input.", {
          title: "Microphone blocked",
        });
      } else if (event.error !== "no-speech" && event.error !== "aborted") {
        showAlert("Voice input failed. Please try again.", { title: "Voice input error" });
      }
    };

    recognition.onend = () => {
      setIsListening(false);
      const finalText = finalTranscript.trim();
      if (finalText) {
        sendMessage(finalText);
      }
    };

    try {
      recognition.start();
      setIsListening(true);
    } catch (error) {
      console.error("Failed to start voice recognition:", error);
    }
  };

  const speakText = (text) => {
    if (!autoSpeak || !text || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(String(text));
    window.speechSynthesis.speak(utterance);
  };

  const toggleAutoSpeak = () => {
    setAutoSpeak((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("agentChat.autoSpeak", String(next));
      } catch {
        // ignore - localStorage may be unavailable
      }
      if (!next) {
        window.speechSynthesis?.cancel();
      }
      return next;
    });
  };

  useEffect(() => {
    const loadAgent = async () => {
      try {
        const jwt = getToken();
        const response = await fetch(URL.AGENT_CONFIGURATION(agentId), {
          headers: {
            "Content-Type": "application/json",
            ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
          },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(apiErrorMessage(data, response.status));
        }
        setAgent(data.agent || null);
      } catch (error) {
        console.error("Failed to load custom agent:", error);
      }
    };

    loadAgent();
  }, [agentId]);

  useEffect(() => {
    const loadChatSessions = async () => {
      try {
        const jwt = getToken();
        const response = await fetch(URL.CHAT_SESSIONS(agentId), {
          headers: {
            "Content-Type": "application/json",
            ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
          },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(apiErrorMessage(data, response.status));
        }
        setChatSessions(sortSessionsByUpdatedAt(Array.isArray(data.sessions) ? data.sessions : []));
      } catch (error) {
        console.error("Failed to load chat sessions:", error);
      }
    };

    if (agentId) {
      setMessages([]);
      setActiveChatId(null);
      setShownFormIds([]);
      setDismissedFormIds([]);
      setHandledDatabaseConfirmations([]);
      updateSelectedDatabaseProfile("auto");
      setInputMessage("");
      loadChatSessions();
      try {
        recognitionRef.current?.stop();
      } catch {
        // ignore - recognition may already be stopped
      }
      window.speechSynthesis?.cancel();
      setIsListening(false);
    }
  }, [agentId]);

  const messageToHistory = (msg) => {
    if (msg.type === "tool_result") {
      return { role: "tool_result", content: msg.text };
    }
    return {
      role: msg.type === "user" ? "user" : "assistant",
      content: msg.text,
    };
  };

  const authHeaders = () => {
    const jwt = getToken();
    return {
      "Content-Type": "application/json",
      ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
    };
  };

  const upsertChatSession = (session) => {
    if (!session?.id) return;
    setChatSessions((prev) => {
      const next = prev.some((item) => String(item.id) === String(session.id))
        ? prev.map((item) => String(item.id) === String(session.id) ? { ...item, ...session } : item)
        : [session, ...prev];
      return sortSessionsByUpdatedAt(next);
    });
  };

  const startNewChat = () => {
    setActiveChatId(null);
    setMessages([]);
    setInputMessage("");
    setShownFormIds([]);
    setDismissedFormIds([]);
    setHandledDatabaseConfirmations([]);
    updateSelectedDatabaseProfile("auto");
    setOpenMenuId(null);
    setRenamingChatId(null);
  };

  const ensureActiveChatSession = async () => {
    if (activeChatId) return activeChatId;
    const response = await fetch(URL.CHAT_SESSIONS(agentId), {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(data, response.status));
    }
    const session = data.session;
    if (!session?.id) {
      throw new Error("Chat session was not created.");
    }
    setActiveChatId(session.id);
    upsertChatSession(session);
    return session.id;
  };

  const openChatSession = async (session) => {
    if (!session?.id || isLoading) return;
    setIsHistoryLoading(true);
    setOpenMenuId(null);
    setRenamingChatId(null);
    try {
      const response = await fetch(URL.CHAT_HISTORY(agentId, session.id), {
        headers: authHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, response.status));
      }
      const loadedMessages = markSubmittedForms(Array.isArray(data.messages) ? data.messages : []);
      setMessages(loadedMessages);
      setActiveChatId(session.id);
      setShownFormIds([]);
      setDismissedFormIds([]);
      setHandledDatabaseConfirmations([]);
      updateSelectedDatabaseProfile("auto");
      setInputMessage("");
    } catch (error) {
      console.error("Failed to open chat session:", error);
    } finally {
      setIsHistoryLoading(false);
    }
  };

  const renameChatSession = async (sessionId) => {
    const title = renameValue.trim();
    if (!sessionId || !title) {
      setRenamingChatId(null);
      return;
    }
    try {
      const response = await fetch(URL.CHAT_SESSION(agentId, sessionId), {
        method: "PATCH",
        headers: authHeaders(),
        body: JSON.stringify({ title }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, response.status));
      }
      upsertChatSession(data.session);
    } catch (error) {
      console.error("Failed to rename chat session:", error);
    } finally {
      setRenamingChatId(null);
      setOpenMenuId(null);
      setRenameValue("");
    }
  };

  const deleteChatSession = async (sessionId) => {
    if (!sessionId) return;
    try {
      const response = await fetch(URL.CHAT_SESSION(agentId, sessionId), {
        method: "DELETE",
        headers: authHeaders(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, response.status));
      }
      setChatSessions((prev) => prev.filter((item) => String(item.id) !== String(sessionId)));
      if (String(activeChatId) === String(sessionId)) {
        startNewChat();
      }
    } catch (error) {
      console.error("Failed to delete chat session:", error);
    } finally {
      setOpenMenuId(null);
    }
  };

  const sendMessage = async (message, options = {}) => {
    if (isAgentExpired(agent)) {
      return;
    }
    const text = (message || "").trim();
    if (!text) return;

    const appendUser = options.appendUser !== false;
    const historyMessages = options.historyMessages || messages;
    let chatSessionId = options.chatSessionId || activeChatId;
    if (appendUser) {
      setMessages((prev) => [...prev, { type: "user", text }]);
      setInputMessage("");
    }
    setIsLoading(true);

    try {
      if (!chatSessionId) {
        chatSessionId = await ensureActiveChatSession();
      }
      const jwt = getToken();
      const history = historyMessages.slice(-10).map(messageToHistory);
      const liveDatabaseProfileSelection = databaseProfileSelectRef.current?.value || selectedDatabaseProfileRef.current;
      const databaseProfileSelection = Object.prototype.hasOwnProperty.call(options, "databaseProfile")
        ? options.databaseProfile
        : liveDatabaseProfileSelection;
      const databaseProfileIndex = databaseProfileSelection === "auto" || databaseProfileSelection === null || databaseProfileSelection === undefined || databaseProfileSelection === ""
        ? null
        : Number(databaseProfileSelection);
      const databaseProfile = Number.isFinite(databaseProfileIndex)
        ? configuredDatabaseProfiles(agent).find((profile) => Number(profile.index) === databaseProfileIndex)
        : null;
      const response = await fetch(URL.CHAT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        },
        body: JSON.stringify({
          agent_id: Number(agentId),
          chat_session_id: chatSessionId,
          question: text,
          suppress_user_message: Boolean(options.suppressUserMessage),
          database_profile_index: Number.isFinite(databaseProfileIndex) ? databaseProfileIndex : null,
          database_profile_name: databaseProfile?.name || null,
          history,
          shown_form_ids: shownFormIds,
          dismissed_form_ids: dismissedFormIds,
        }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(apiErrorMessage(data, response.status));
      }

      const isRenderForm = data.type === "render_form" && data.form;
      if (isRenderForm && data.form_id) {
        setShownFormIds((prev) => prev.includes(data.form_id) ? prev : [...prev, data.form_id]);
      }
      if (data.chat_session_id) {
        const session = {
          id: data.chat_session_id,
          title: data.chat_session_title || deriveChatTitle(text),
          updated_at: new Date().toISOString(),
        };
        setActiveChatId(data.chat_session_id);
        upsertChatSession(session);
      }
      const botText = data.answer || data.confirmationMessage || data.message || "No answer returned.";
      setMessages((prev) => [
        ...prev,
        {
          type: "bot",
          actionType: isRenderForm ? "render_form" : "message",
          text: botText,
          sources: Array.isArray(data.sources) ? data.sources : [],
          quickReplies: Array.isArray(data.quick_replies) ? data.quick_replies : [],
          apiResults: Array.isArray(data.api_results) ? data.api_results : [],
          form: isRenderForm ? data.form : null,
        },
      ]);
      speakText(botText);
    } catch (error) {
      console.error("Chat error:", error);
      setMessages((prev) => [
        ...prev,
        { type: "bot", text: `Sorry - ${error.message || "I couldn't get an answer."}` },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(inputMessage);
    }
  };

  const handleFormSubmit = async (form, values) => {
    if (isAgentExpired(agent)) {
      return;
    }
    const jwt = getToken();
    const response = await fetch(URL.FORM_SUBMISSIONS(agentId), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
      },
      body: JSON.stringify({
        form_id: form?.id,
        values,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(apiErrorMessage(data, response.status));
    }

    const summary = (form?.fields || [])
      .map((field) => {
        const value = values[field.name];
        const formattedValue = typeof value === "boolean" ? (value ? "Yes" : "No") : value;
        return `${field.label}: ${formattedValue || "-"}`;
      })
      .join("\n");

    const userSubmissionMessage = {
      type: "user",
      text: `Submitted ${form?.title || "form"}\n${summary}`,
    };
    const toolResultMessage = {
      type: "tool_result",
      text: JSON.stringify({
        formId: form?.id,
        formTitle: form?.title || "form",
        delivery: data.delivery,
        status: "success",
        values,
      }),
    };
    const nextMessages = markSubmittedForms([...messages, userSubmissionMessage, toolResultMessage]);
    setMessages(nextMessages);
    await sendMessage("__tool_result__", {
      appendUser: false,
      historyMessages: nextMessages,
    });
  };

  const handleFormDismiss = (form) => {
    if (form?.id) {
      setDismissedFormIds((prev) => prev.includes(form.id) ? prev : [...prev, form.id]);
    }
    setMessages((prev) => [
      ...prev,
      {
        type: "user",
        text: `Dismissed ${form?.title || "the form"}`,
      },
      {
        type: "bot",
        text: "No problem. I can reopen it later if you ask.",
      },
    ]);
  };

  const sendDatabaseConfirmationAction = (confirmation, action) => {
    const command = databaseConfirmationCommand(confirmation, action);
    if (!command) return;
    const confirmationId = databaseConfirmationId(confirmation);
    if (confirmationId) {
      setHandledDatabaseConfirmations((prev) => [
        ...prev.filter((item) => String(item?.id || item) !== confirmationId),
        { id: confirmationId, status: action === "cancel" ? "cancelled" : "submitted" },
      ]);
    }
    sendMessage(command, {
      appendUser: false,
      suppressUserMessage: true,
    });
  };

  const filteredChatSessions = chatSessions.filter((session) =>
    String(session.title || "New chat").toLowerCase().includes(chatSearch.trim().toLowerCase())
  );

  const chatSidebar = (
    <aside className="agent-chat-sidebar" aria-label="Chat history">
      <div className="agent-chat-sidebar-header">
        <div>
          <div className="agent-chat-sidebar-title">{agent?.name || "Agent chats"}</div>
          <div className="agent-chat-sidebar-caption">Chat history</div>
        </div>
      </div>

      <button type="button" className="agent-chat-sidebar-action primary" onClick={startNewChat}>
        <FiPlus />
        <span>New chat</span>
      </button>

      <label className="agent-chat-sidebar-search">
        <FiSearch />
        <input
          type="search"
          value={chatSearch}
          onChange={(event) => setChatSearch(event.target.value)}
          placeholder="Search chats"
        />
        {chatSearch && (
          <button type="button" onClick={() => setChatSearch("")} aria-label="Clear chat search">
            <FiX />
          </button>
        )}
      </label>

      <div className="agent-chat-sidebar-section-label">Previous chats</div>

      <div className="agent-chat-session-list">
        {filteredChatSessions.length === 0 ? (
          <div className="agent-chat-session-empty">
            {chatSearch ? "No matching chats" : "No previous chats"}
          </div>
        ) : (
          filteredChatSessions.map((session) => {
            const isActive = String(activeChatId) === String(session.id);
            const isRenaming = String(renamingChatId) === String(session.id);
            return (
              <div key={session.id} className={`agent-chat-session-row${isActive ? " active" : ""}`}>
                {isRenaming ? (
                  <form
                    className="agent-chat-rename-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      renameChatSession(session.id);
                    }}
                  >
                    <input
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      onBlur={() => renameChatSession(session.id)}
                      autoFocus
                    />
                  </form>
                ) : (
                  <button
                    type="button"
                    className="agent-chat-session-open"
                    onClick={() => openChatSession(session)}
                    title={session.title || "New chat"}
                  >
                    <FiMessageSquare />
                    <span>{session.title || "New chat"}</span>
                  </button>
                )}

                {!isRenaming && (
                  <div className="agent-chat-session-menu-wrap">
                    <button
                      type="button"
                      className="agent-chat-session-menu-button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setOpenMenuId((current) => String(current) === String(session.id) ? null : session.id);
                      }}
                      aria-label={`Options for ${session.title || "chat"}`}
                    >
                      <FiMoreVertical />
                    </button>
                    {String(openMenuId) === String(session.id) && (
                      <div className="agent-chat-session-menu">
                        <button
                          type="button"
                          onClick={() => {
                            setRenamingChatId(session.id);
                            setRenameValue(session.title || "New chat");
                            setOpenMenuId(null);
                          }}
                        >
                          <FiEdit3 />
                          <span>Rename</span>
                        </button>
                        <button
                          type="button"
                          className="danger"
                          onClick={async () => {
                            const confirmed = await showConfirm(
                              "This conversation and its messages will be permanently removed.",
                              {
                                title: "Delete chat?",
                                confirmLabel: "Delete chat",
                                variant: "danger",
                              }
                            );
                            if (confirmed) deleteChatSession(session.id);
                          }}
                        >
                          <FiTrash2 />
                          <span>Delete</span>
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );

  const expired = isAgentExpired(agent);
  const databaseProfiles = configuredDatabaseProfiles(agent);
  const inputPlaceholder = expired
    ? (userRole === "admin"
      ? "This agent's subscription has expired. Please return to the dashboard to repay."
      : "This agent's subscription has expired. Please contact your administrator to reactivate it.")
    : "Ask about this agent's documents...";

  return (
    <AgentChatShell
      sidebar={chatSidebar}
    >
      <div className="agent-native-chat">
        {expired && (
          <div className="agent-expired-banner" style={{
            background: "rgba(239, 68, 68, 0.12)",
            color: "#f87171",
            borderBottom: "1px solid rgba(239, 68, 68, 0.2)",
            padding: "14px 20px",
            fontSize: "13px",
            fontWeight: "600",
            textAlign: "center",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "8px",
            zIndex: 100
          }}>
            <span>⚠️ Expired Subscription. This agent's active period has ended.</span>
          </div>
        )}
        <div className="agent-native-messages">
          {messages.length === 0 && <div className="agent-native-empty" />}

          {messages.map((msg, index) => {
            if (msg.type === "tool_result") return null;
            const pendingDatabaseConfirmation = msg.type === "bot" ? databaseConfirmationFromMessage(msg) : null;
            const pendingDatabaseConfirmationId = databaseConfirmationId(pendingDatabaseConfirmation);
            const localConfirmationStatus = handledDatabaseConfirmations.find((item) =>
              String(item?.id || item) === pendingDatabaseConfirmationId
            )?.status;
            const databaseConfirmationStatus =
              databaseConfirmationStatusFromMessages(messages, index, pendingDatabaseConfirmationId) ||
              localConfirmationStatus ||
              "pending";
            const messageClassName = [
              "agent-native-message",
              msg.type,
              msg.form ? "has-form" : "",
              pendingDatabaseConfirmation ? "has-db-confirmation" : "",
            ].filter(Boolean).join(" ");

            return (
              <div key={`${msg.type}-${index}`} className={messageClassName}>
                {msg.type === "bot"
                  ? (!pendingDatabaseConfirmation && <FormattedMessage text={msg.text} />)
                  : <div>{msg.text}</div>}
                {msg.type === "bot" && uniqueSourceLabels(msg.sources).length > 0 && !isAssignedResourceRefusal(msg.text) && (
                  <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
                    <strong>Sources:</strong>{" "}
                    {uniqueSourceLabels(msg.sources).join(", ")}
                  </div>
                )}
                {pendingDatabaseConfirmation && (
                  <DatabaseConfirmationCard
                    confirmation={pendingDatabaseConfirmation}
                    disabled={isLoading || expired}
                    status={databaseConfirmationStatus}
                    onRun={() => sendDatabaseConfirmationAction(pendingDatabaseConfirmation, "run")}
                    onCancel={() => sendDatabaseConfirmationAction(pendingDatabaseConfirmation, "cancel")}
                  />
                )}
                {msg.type === "bot" && !pendingDatabaseConfirmation && msg.quickReplies && msg.quickReplies.length > 0 && (
                  <div className="agent-quick-replies">
                    {msg.quickReplies.map((reply) => (
                      <button key={reply} type="button" onClick={() => sendMessage(reply)} disabled={isLoading || expired}>
                        {reply}
                      </button>
                    ))}
                  </div>
                )}
                {msg.type === "bot" && msg.form && (
                  <InlineAgentForm
                    form={msg.form}
                    onSubmit={handleFormSubmit}
                    onDismiss={handleFormDismiss}
                    initiallySubmitted={Boolean(msg.formSubmitted)}
                  />
                )}
              </div>
            );
          })}

          {isHistoryLoading && <div className="agent-native-message bot">Loading chat history...</div>}
          {isLoading && <div className="agent-native-message bot">Thinking...</div>}
        </div>

        <div className="agent-native-input-bar">
          <button
            type="button"
            onClick={toggleAutoSpeak}
            className={`agent-native-speak-toggle${autoSpeak ? " active" : ""}`}
            aria-label={autoSpeak ? "Disable read aloud" : "Enable read aloud"}
            title={autoSpeak ? "Voice replies: on" : "Voice replies: off"}
          >
            {autoSpeak ? <FiVolume2 /> : <FiVolumeX />}
          </button>
          {databaseProfiles.length > 1 && (
            <label className="agent-db-profile-picker">
              <span>DB</span>
              <select
                ref={databaseProfileSelectRef}
                value={selectedDatabaseProfile}
                onChange={(e) => updateSelectedDatabaseProfile(e.target.value)}
                disabled={isLoading || expired}
                title="Choose Auto routing or a specific database profile for this chat"
              >
                <option value="auto">Auto</option>
                {databaseProfiles.map((profile) => (
                  <option key={profile.index} value={profile.index}>
                    {profile.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <input
            type="text"
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            onKeyDown={handleKeyPress}
            placeholder={inputPlaceholder}
            disabled={isLoading || expired}
            className="agent-native-input"
          />
          {voiceSupported && (
            <button
              type="button"
              onClick={toggleListening}
              disabled={isLoading || expired}
              className={`agent-native-mic${isListening ? " listening" : ""}`}
              aria-label={isListening ? "Stop voice input" : "Start voice input"}
              title={isListening ? "Listening... click to stop" : "Speak your question"}
            >
              <FiMic />
            </button>
          )}
          <button
            onClick={() => sendMessage(inputMessage)}
            disabled={isLoading || expired || !inputMessage.trim()}
            className="agent-native-send"
            aria-label="Send message"
          >
            <IoSend />
          </button>
        </div>
      </div>
    </AgentChatShell>
  );
};

export default CustomAgentChat;
