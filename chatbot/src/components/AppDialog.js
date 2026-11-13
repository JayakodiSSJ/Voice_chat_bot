import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  FiAlertTriangle,
  FiCheckCircle,
  FiHelpCircle,
  FiInfo,
  FiX,
} from "react-icons/fi";
import "./AppDialog.css";

const AppDialogContext = createContext(null);

const DEFAULTS = {
  alert: {
    title: "Notification",
    confirmLabel: "Got it",
    variant: "info",
  },
  confirm: {
    title: "Confirm action",
    confirmLabel: "Confirm",
    cancelLabel: "Cancel",
    variant: "warning",
  },
  prompt: {
    title: "Enter details",
    confirmLabel: "Save",
    cancelLabel: "Cancel",
    variant: "info",
  },
};

const VARIANT_ICONS = {
  danger: FiAlertTriangle,
  warning: FiAlertTriangle,
  success: FiCheckCircle,
  info: FiInfo,
};

const normalizeOptions = (type, message, options = {}) => ({
  ...DEFAULTS[type],
  ...options,
  type,
  message,
});

export const AppDialogProvider = ({ children }) => {
  const [dialog, setDialog] = useState(null);
  const [inputValue, setInputValue] = useState("");
  const queueRef = useRef([]);
  const resolverRef = useRef(null);
  const returnFocusRef = useRef(null);
  const dialogRef = useRef(null);
  const inputRef = useRef(null);
  const primaryButtonRef = useRef(null);

  const displayNext = useCallback(() => {
    const next = queueRef.current.shift();
    if (!next) {
      setDialog(null);
      window.setTimeout(() => {
        if (returnFocusRef.current?.isConnected) returnFocusRef.current.focus();
        returnFocusRef.current = null;
      }, 0);
      return;
    }

    resolverRef.current = next.resolve;
    setInputValue(String(next.config.defaultValue ?? ""));
    setDialog(next.config);
  }, []);

  const requestDialog = useCallback(
    (config) =>
      new Promise((resolve) => {
        if (!resolverRef.current) returnFocusRef.current = document.activeElement;
        queueRef.current.push({ config, resolve });
        if (!resolverRef.current) displayNext();
      }),
    [displayNext]
  );

  const showAlert = useCallback(
    (message, options) => requestDialog(normalizeOptions("alert", message, options)),
    [requestDialog]
  );

  const showConfirm = useCallback(
    (message, options) => requestDialog(normalizeOptions("confirm", message, options)),
    [requestDialog]
  );

  const showPrompt = useCallback(
    (message, options) => requestDialog(normalizeOptions("prompt", message, options)),
    [requestDialog]
  );

  const settleDialog = useCallback(
    (result) => {
      const resolve = resolverRef.current;
      resolverRef.current = null;
      resolve?.(result);
      displayNext();
    },
    [displayNext]
  );

  const handleCancel = useCallback(() => {
    settleDialog(dialog?.type === "confirm" ? false : null);
  }, [dialog?.type, settleDialog]);

  const handleConfirm = useCallback(() => {
    if (dialog?.type === "prompt") {
      settleDialog(inputValue);
      return;
    }
    settleDialog(dialog?.type === "confirm" ? true : undefined);
  }, [dialog?.type, inputValue, settleDialog]);

  useEffect(() => {
    if (!dialog) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => {
      if (dialog.type === "prompt") inputRef.current?.focus();
      else primaryButtonRef.current?.focus();
    }, 0);

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleCancel();
        return;
      }

      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [dialog, handleCancel]);

  const contextValue = useMemo(
    () => ({ showAlert, showConfirm, showPrompt }),
    [showAlert, showConfirm, showPrompt]
  );

  const Icon = dialog ? VARIANT_ICONS[dialog.variant] || FiHelpCircle : FiInfo;

  return (
    <AppDialogContext.Provider value={contextValue}>
      {children}
      {dialog &&
        createPortal(
          <div
            className="app-dialog-backdrop"
            role="presentation"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) handleCancel();
            }}
          >
            <section
              ref={dialogRef}
              className={`app-dialog app-dialog--${dialog.variant}`}
              role={dialog.type === "alert" ? "alertdialog" : "dialog"}
              aria-modal="true"
              aria-labelledby="app-dialog-title"
              aria-describedby="app-dialog-description"
            >
              <button
                type="button"
                className="app-dialog__close"
                onClick={handleCancel}
                aria-label="Close dialog"
              >
                <FiX aria-hidden="true" />
              </button>

              <div className="app-dialog__heading">
                <span className="app-dialog__icon" aria-hidden="true">
                  <Icon />
                </span>
                <div>
                  <p className="app-dialog__eyebrow">
                    {dialog.type === "prompt" ? "Input required" : dialog.type === "confirm" ? "Please confirm" : "System message"}
                  </p>
                  <h2 id="app-dialog-title">{dialog.title}</h2>
                </div>
              </div>

              <p id="app-dialog-description" className="app-dialog__message">
                {dialog.message}
              </p>

              {dialog.type === "prompt" && (
                <label className="app-dialog__field">
                  {dialog.inputLabel && <span>{dialog.inputLabel}</span>}
                  {dialog.multiline ? (
                    <textarea
                      ref={inputRef}
                      value={inputValue}
                      onChange={(event) => setInputValue(event.target.value)}
                      placeholder={dialog.placeholder}
                      rows={dialog.rows || 4}
                    />
                  ) : (
                    <input
                      ref={inputRef}
                      type={dialog.inputType || "text"}
                      inputMode={dialog.inputMode}
                      min={dialog.min}
                      value={inputValue}
                      onChange={(event) => setInputValue(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          handleConfirm();
                        }
                      }}
                      placeholder={dialog.placeholder}
                    />
                  )}
                  {dialog.helperText && <small>{dialog.helperText}</small>}
                </label>
              )}

              <div className="app-dialog__actions">
                {dialog.type !== "alert" && (
                  <button
                    type="button"
                    className="app-dialog__button app-dialog__button--secondary"
                    onClick={handleCancel}
                  >
                    {dialog.cancelLabel}
                  </button>
                )}
                <button
                  ref={primaryButtonRef}
                  type="button"
                  className="app-dialog__button app-dialog__button--primary"
                  onClick={handleConfirm}
                >
                  {dialog.confirmLabel}
                </button>
              </div>
            </section>
          </div>,
          document.body
        )}
    </AppDialogContext.Provider>
  );
};

export const useAppDialog = () => {
  const context = useContext(AppDialogContext);
  if (!context) {
    throw new Error("useAppDialog must be used within an AppDialogProvider");
  }
  return context;
};
