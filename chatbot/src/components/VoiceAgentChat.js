import React, { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { FiMic, FiSquare, FiVolume2, FiVolumeX } from "react-icons/fi";
import AgentChatShell from "./AgentChatShell";
import AvatarStage from "./voice/AvatarStage";
import { VOICE_LANGUAGES } from "./voice/languages";
import { createPcmWavRecorder, stopAudioPlayback } from "./voice/audioUtils";
import { createVoiceActivityDetector } from "./voice/voiceActivity";
import { fetchTtsWithRetry, quotaErrorMessage } from "./voice/voiceRequests";
import { URL } from "../urls";
import { getToken } from "../auth/authClient";
import "./VoiceAgentChat.css";

const apiErrorMessage = (data, status) => data?.message || data?.error || `HTTP ${status}`;

const logVoiceDiagnostic = (event, details = {}) => {
  if (process.env.NODE_ENV === "development") {
    console.info("[voice]", { event, ...details });
  }
};

const getWavMetadata = async (blob) => {
  if (!blob || blob.size < 44) return null;
  const buffer = await blob.arrayBuffer();
  const view = new DataView(buffer);
  if (view.getUint32(0, false) !== 0x52494646 || view.getUint32(8, false) !== 0x57415645) {
    return null;
  }
  const sampleRate = view.getUint32(24, true);
  const channels = view.getUint16(22, true);
  const bitsPerSample = view.getUint16(34, true);
  const dataBytes = view.getUint32(40, true);
  const bytesPerSecond = sampleRate * channels * (bitsPerSample / 8);
  return {
    sampleRate,
    channels,
    durationSeconds: bytesPerSecond ? Number((dataBytes / bytesPerSecond).toFixed(2)) : null,
  };
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

const cleanForSpeech = (text) =>
  String(text || "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();

// Auto-stop tuning: how loud counts as speech, how long a silence has to last
// before we treat it as "done talking", and a hard cap so listening can never
// hang open forever if the mic picks up constant background noise.
const SPEECH_RMS_THRESHOLD = 0.015;
const SPEECH_START_MS = 180;
const SILENCE_STOP_MS = 1000;
const NO_SPEECH_STOP_MS = 8000;
const MAX_RECORDING_MS = 20000;
const computeRms = (dataArray) => {
  let sumSquares = 0;
  for (let i = 0; i < dataArray.length; i += 1) {
    const normalized = (dataArray[i] - 128) / 128;
    sumSquares += normalized * normalized;
  }
  return Math.sqrt(sumSquares / dataArray.length);
};

const YES_WORDS = ["yes", "yeah", "yep", "yup", "correct", "right", "confirm", "confirmed", "ok", "okay", "sure", "go ahead", "submit", "sounds good"];
const NO_WORDS = ["no", "nope", "not right", "wrong", "incorrect", "change", "edit", "redo", "cancel", "hold on", "wait"];

const parseYesNo = (text) => {
  const normalized = String(text || "").toLowerCase();
  if (YES_WORDS.some((word) => normalized.includes(word))) return "yes";
  if (NO_WORDS.some((word) => normalized.includes(word))) return "no";
  return null;
};

const matchesFieldLabel = (text, label) => {
  const normalizedText = String(text || "").toLowerCase().trim();
  const normalizedLabel = String(label || "").toLowerCase().trim();
  if (!normalizedText || !normalizedLabel) return false;
  return normalizedText.includes(normalizedLabel) || normalizedLabel.includes(normalizedText);
};

const matchOption = (text, options) => {
  const normalizedText = String(text || "").toLowerCase();
  return (
    options.find((option) => {
      const normalizedOption = String(option || "").toLowerCase();
      return normalizedOption && (normalizedText.includes(normalizedOption) || normalizedOption.includes(normalizedText));
    }) || null
  );
};

// Mirrors controlTypeForField/inputTypeForField in CustomAgentChat.js's InlineAgentForm.
const validateFieldAnswer = (field, rawText) => {
  const text = String(rawText || "").trim();
  const controlType = String(field?.inputType || field?.type || "text").toLowerCase();
  const options = Array.isArray(field?.options) ? field.options : [];

  if (!text) {
    return field?.required ? { ok: false, reason: "empty" } : { ok: true, value: "" };
  }

  if (options.length && (controlType.includes("dropdown") || controlType.includes("select") || controlType.includes("radio"))) {
    const matched = matchOption(text, options);
    return matched ? { ok: true, value: matched } : { ok: false, reason: "no_match" };
  }

  if (controlType.includes("checkbox")) {
    const yn = parseYesNo(text);
    return yn ? { ok: true, value: yn === "yes" } : { ok: false, reason: "unclear" };
  }

  return { ok: true, value: text };
};

const VoiceAgentChat = ({ userRole }) => {
  const { agentId } = useParams();
  const [agent, setAgent] = useState(null);
  const [agentState, setAgentState] = useState("idle");
  const [conversationActive, setConversationActive] = useState(false);
  const [serviceDisconnected, setServiceDisconnected] = useState(false);
  const [language, setLanguage] = useState("auto");
  const [turns, setTurns] = useState([]);
  const [error, setError] = useState("");
  const [formSession, setFormSession] = useState(null);
  const [manualFieldValue, setManualFieldValue] = useState("");
  const [micSupported] = useState(() => Boolean(
    navigator.mediaDevices?.getUserMedia &&
    (window.AudioContext || window.webkitAudioContext)?.prototype?.createScriptProcessor
  ));

  const activeChatIdRef = useRef(null);
  const continuousSessionRef = useRef(false);
  const voiceSessionIdRef = useRef(0);
  const stopConversationRef = useRef(null);
  const chatRequestControllerRef = useRef(null);
  const transcriptionRequestControllerRef = useRef(null);
  const speechRequestControllerRef = useRef(null);
  const resumeListeningTimeoutRef = useRef(null);
  const turnsRef = useRef([]);
  const agentRef = useRef(null);
  const formSessionRef = useRef(null);
  const streamRef = useRef(null);
  const pcmRecorderRef = useRef(null);
  const pcmRecorderSessionRef = useRef(null);
  const audioPlayerRef = useRef(null);
  const audioObjectUrlRef = useRef(null);
  const speechAudioContextRef = useRef(null);
  const speechAudioSourceRef = useRef(null);
  const speechAnalyserRef = useRef(null);
  const speechAnalyserDataRef = useRef(null);
  const speechAnalyserFrameRef = useRef(null);
  const speechAudioLevelRef = useRef(0);
  const audioContextRef = useRef(null);
  const voiceActivityDetectorRef = useRef(null);
  const silenceRafRef = useRef(null);
  const hasSpokenRef = useRef(false);
  const recordingTimeoutRef = useRef(null);
  const recordingStartedAtRef = useRef(null);
  const speechActiveRef = useRef(false);
  const recordingSetupRef = useRef(false);

  const setConversationMode = (active, updateUi = true) => {
    continuousSessionRef.current = active;
    if (updateUi) setConversationActive(active);
    if (!active && resumeListeningTimeoutRef.current !== null) {
      window.clearTimeout(resumeListeningTimeoutRef.current);
      resumeListeningTimeoutRef.current = null;
    }
  };

  const resumeConversationListening = () => {
    if (!continuousSessionRef.current) return;
    logVoiceDiagnostic("session_recovery", { nextState: "listening" });
    if (resumeListeningTimeoutRef.current !== null) {
      window.clearTimeout(resumeListeningTimeoutRef.current);
    }
    setAgentState("recovering");
    resumeListeningTimeoutRef.current = window.setTimeout(() => {
      resumeListeningTimeoutRef.current = null;
      if (continuousSessionRef.current) {
        setAgentState("listening");
        void startRecording();
      }
    }, 500);
  };

  const prepareSpeechAnalyser = async (audioElement) => {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor || !audioElement) return false;
    try {
      if (!speechAudioContextRef.current) {
        speechAudioContextRef.current = new AudioContextCtor();
        speechAnalyserRef.current = speechAudioContextRef.current.createAnalyser();
        speechAnalyserRef.current.fftSize = 512;
        speechAnalyserDataRef.current = new Uint8Array(speechAnalyserRef.current.fftSize);
        speechAudioSourceRef.current = speechAudioContextRef.current.createMediaElementSource(audioElement);
        speechAudioSourceRef.current.connect(speechAnalyserRef.current);
        speechAnalyserRef.current.connect(speechAudioContextRef.current.destination);
      }
      if (speechAudioContextRef.current.state === "suspended") {
        await speechAudioContextRef.current.resume();
      }
      return true;
    } catch (err) {
      console.warn("Audio-synchronized avatar movement is unavailable:", err);
      speechAudioLevelRef.current = 0;
      return false;
    }
  };

  const startSpeechAnalyser = (audioElement) => {
    if (!speechAnalyserRef.current || !speechAnalyserDataRef.current) return;
    if (speechAnalyserFrameRef.current !== null) {
      cancelAnimationFrame(speechAnalyserFrameRef.current);
    }
    const sample = () => {
      if (audioElement.paused || audioElement.ended) {
        speechAudioLevelRef.current = 0;
        speechAnalyserFrameRef.current = null;
        return;
      }
      speechAnalyserRef.current.getByteTimeDomainData(speechAnalyserDataRef.current);
      const rms = computeRms(speechAnalyserDataRef.current);
      speechAudioLevelRef.current = Math.min(rms * 5, 1);
      speechAnalyserFrameRef.current = requestAnimationFrame(sample);
    };
    speechAnalyserFrameRef.current = requestAnimationFrame(sample);
  };

  const stopSpeechAnalyser = () => {
    speechAudioLevelRef.current = 0;
    if (speechAnalyserFrameRef.current !== null) {
      cancelAnimationFrame(speechAnalyserFrameRef.current);
      speechAnalyserFrameRef.current = null;
    }
  };


  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);

  useEffect(() => {
    agentRef.current = agent;
  }, [agent]);

  useEffect(() => {
    let mounted = true;
    const stopOnPageExit = () => stopConversationRef.current?.({ updateUi: false });
    window.addEventListener("pagehide", stopOnPageExit);
    window.addEventListener("beforeunload", stopOnPageExit);
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
        if (!response.ok) throw new Error(apiErrorMessage(data, response.status));
        if (mounted) setAgent(data.agent || null);
      } catch (err) {
        console.error("Failed to load custom agent:", err);
      }
    };
    const checkVoiceReadiness = async () => {
      try {
        const jwt = getToken();
        const response = await fetch(URL.VOICE_READY, {
          headers: {
            ...(jwt ? { Authorization: ["Bearer", jwt].join(" ") } : {}),
          },
        });
        const data = await response.json().catch(() => ({}));
        if (mounted) setServiceDisconnected(!response.ok || data.ready !== true);
      } catch (err) {
        console.error("Voice readiness check failed:", err);
        if (mounted) setServiceDisconnected(true);
      }
    };
    loadAgent();
    checkVoiceReadiness();

    return () => {
      mounted = false;
      window.removeEventListener("pagehide", stopOnPageExit);
      window.removeEventListener("beforeunload", stopOnPageExit);
      stopConversationRef.current?.({ updateUi: false });
      stopSpeechAnalyser();
      speechAudioContextRef.current?.close().catch(() => {});
      speechAudioContextRef.current = null;
      speechAudioSourceRef.current = null;
      speechAnalyserRef.current = null;
      if (silenceRafRef.current !== null) {
        cancelAnimationFrame(silenceRafRef.current);
        silenceRafRef.current = null;
      }
      if (recordingTimeoutRef.current !== null) {
        window.clearTimeout(recordingTimeoutRef.current);
        recordingTimeoutRef.current = null;
      }
      if (resumeListeningTimeoutRef.current !== null) {
        window.clearTimeout(resumeListeningTimeoutRef.current);
        resumeListeningTimeoutRef.current = null;
      }
      audioContextRef.current?.close().catch(() => {});
      audioContextRef.current = null;
      if (audioObjectUrlRef.current) {
        window.URL.revokeObjectURL(audioObjectUrlRef.current);
        audioObjectUrlRef.current = null;
      }
    };
  }, [agentId]);

  const ensureActiveChatSession = async (signal) => {
    if (activeChatIdRef.current) return activeChatIdRef.current;
    const response = await fetch(URL.CHAT_SESSIONS(agentId), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: JSON.stringify({}),
      signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiErrorMessage(data, response.status));
    const sessionId = data.session?.id;
    if (!sessionId) throw new Error("Chat session was not created.");
    activeChatIdRef.current = sessionId;
    return sessionId;
  };

  const speak = async (text, { autoListen = false, onEnd = null } = {}) => {
    const sessionId = voiceSessionIdRef.current;
    const sessionIsCurrent = () => sessionId === voiceSessionIdRef.current;
    const spoken = cleanForSpeech(text);
    const finish = () => {
      if (!sessionIsCurrent()) return;
      speechActiveRef.current = false;
      stopSpeechAnalyser();
      if (autoListen) resumeConversationListening();
      else setAgentState("idle");
      onEnd?.();
    };
    if (!spoken) {
      finish();
      return;
    }

    speechActiveRef.current = true;
    const releaseAudioUrl = () => {
      if (audioObjectUrlRef.current) {
        window.URL.revokeObjectURL(audioObjectUrlRef.current);
        audioObjectUrlRef.current = null;
      }
    };
    setAgentState("processing");
    speechRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    speechRequestControllerRef.current = requestController;
    try {
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
      const currentAudio = audioPlayerRef.current;
      if (currentAudio) {
        currentAudio.onended = null;
        currentAudio.onerror = null;
        currentAudio.pause();
      }
      releaseAudioUrl();

      const jwt = getToken();
      const response = await fetchTtsWithRetry(URL.VOICE_TTS, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        },
        body: JSON.stringify({ text: spoken }),
        signal: requestController.signal,
      });
      const provider = response.headers?.get?.("X-Speech-Provider") || "unknown";
      logVoiceDiagnostic("tts_provider_selected", { provider });
      logVoiceDiagnostic("tts_response", {
        status: response.status,
      });
      if (!sessionIsCurrent()) return;
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        logVoiceDiagnostic("tts_failed", { status: response.status, errorCode: data.error || "unknown" });
        if (response.status === 429) {
          const error = new Error(quotaErrorMessage(data, response.headers?.get?.("Retry-After")));
          error.status = response.status;
          error.quotaExceeded = true;
          throw error;
        }
        throw new Error(apiErrorMessage(data, response.status));
      }

      const audioBlob = await response.blob();
      if (!sessionIsCurrent()) return;
      if (!audioBlob.size || !audioBlob.type.startsWith("audio/")) {
        throw new Error("The speech service returned an empty or unsupported audio response.");
      }
      const audioEl = audioPlayerRef.current;
      if (!audioEl) throw new Error("Audio playback is unavailable.");

      const audioUrl = window.URL.createObjectURL(audioBlob);
      audioObjectUrlRef.current = audioUrl;
      audioEl.src = audioUrl;
      audioEl.load();
      audioEl.onended = () => {
        logVoiceDiagnostic("playback_completed");
        releaseAudioUrl();
        finish();
      };
      audioEl.onerror = () => {
        logVoiceDiagnostic("playback_failed");
        console.error("The generated speech audio could not be played.");
        setError("The generated speech audio could not be played. I'll keep listening.");
        stopSpeechAnalyser();
        releaseAudioUrl();
        finish();
      };

      await prepareSpeechAnalyser(audioEl);
      if (!sessionIsCurrent()) return;
      setAgentState("speaking");
      await audioEl.play();
      if (!sessionIsCurrent()) return;
      logVoiceDiagnostic("playback_started", { bytes: audioBlob.size });
      startSpeechAnalyser(audioEl);
    } catch (err) {
      if (err.name !== "AbortError" && sessionIsCurrent()) {
        console.error("Voice playback error:", err);
        if (err.quotaExceeded) {
          stopConversation();
          setError(err.message);
          setAgentState("stopped");
        } else {
          setError(err.message || "Speech playback is unavailable. I'll keep listening.");
          finish();
        }
      }
    } finally {
      if (speechRequestControllerRef.current === requestController) {
        speechRequestControllerRef.current = null;
      }
    }
  };
  const reportError = (message, { resumeListening = true } = {}) => {
    const text = message || "Something went wrong.";
    setError(text);
    if (resumeListening && continuousSessionRef.current) {
      resumeConversationListening();
    } else {
      setAgentState("error");
    }
  };

  const sendVoiceMessage = async (text) => {
    if (isAgentExpired(agentRef.current)) return;
    const question = (text || "").trim();
    if (!question) return;

    const sessionId = voiceSessionIdRef.current;
    chatRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    chatRequestControllerRef.current = requestController;
    setAgentState("processing");
    setError("");

    try {
      const chatSessionId = await ensureActiveChatSession(requestController.signal);
      if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
      const jwt = getToken();
      const history = turnsRef.current.slice(-10).map((turn) => ({
        role: turn.role,
        content: turn.text,
      }));

      logVoiceDiagnostic("chat_request_started");
      const response = await fetch(URL.CHAT, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        },
        body: JSON.stringify({
          agent_id: Number(agentId),
          chat_session_id: chatSessionId,
          question,
          suppress_user_message: false,
          database_profile_index: null,
          database_profile_name: null,
          history,
          response_mode: "voice",
          shown_form_ids: [],
          dismissed_form_ids: [],
        }),
        signal: requestController.signal,
      });

      const data = await response.json().catch(() => ({}));
      logVoiceDiagnostic("chat_response", {
        status: response.status,
        responseType: typeof data.type === "string" ? data.type : "answer",
      });
      if (sessionId !== voiceSessionIdRef.current) return;

      if (!response.ok) {
        if (response.status >= 500) setServiceDisconnected(true);
        if (response.status === 429) {
          const error = new Error(quotaErrorMessage(data, response.headers?.get?.("Retry-After")));
          error.status = response.status;
          error.quotaExceeded = true;
          throw error;
        }
        throw new Error(apiErrorMessage(data, response.status));
      }
      setServiceDisconnected(false);

      if (data.type === "render_form" && data.form) {
        beginFormSession(data.form, data.confirmationMessage || data.message || data.answer);
        return;
      }

      const answer = data.answer || data.confirmationMessage || data.message || "Sorry, I don't have an answer for that.";

      setTurns((prev) => [...prev, { role: "assistant", text: answer }]);
      speak(answer, { autoListen: true });
    } catch (err) {
      if (sessionId !== voiceSessionIdRef.current) return;

      console.error("Voice chat error:", err);
      if (err instanceof TypeError) setServiceDisconnected(true);
      reportError(err.message || "I'm having a little trouble answering that. Please try again.");
    } finally {
      if (chatRequestControllerRef.current === requestController) {
        chatRequestControllerRef.current = null;
      }
    }
  };

  // --- Voice-driven form fill -------------------------------------------------

  const beginFormSession = (form, introText) => {
    const fields = Array.isArray(form?.fields) ? form.fields : [];
    if (!fields.length) {
      const fallback = introText || "This form has no fields configured.";
      setTurns((prev) => [...prev, { role: "assistant", text: fallback }]);
      speak(fallback, { autoListen: true });
      return;
    }
    const session = { form, fieldIndex: 0, values: {}, phase: "asking", attempts: 0 };
    formSessionRef.current = session;
    setFormSession(session);
    const intro = introText || `Let's fill in ${form.title || "this form"} together.`;
    setTurns((prev) => [...prev, { role: "assistant", text: intro }]);
    speak(intro, { onEnd: () => askField(form, 0) });
  };

  const askField = (form, index) => {
    const field = form.fields[index];
    if (!field) return;
    const options = Array.isArray(field.options) ? field.options : [];
    const optionsHint = options.length ? ` You can say: ${options.join(", ")}.` : "";
    const prompt = `${field.label}${field.required ? "" : " — this one's optional"}.${optionsHint}`;
    setTurns((prev) => [...prev, { role: "assistant", text: prompt }]);
    speak(prompt, { autoListen: true });
  };

  const applyFieldValue = (session, field, value) => {
    const values = { ...session.values, [field.name]: value };
    const nextIndex = session.fieldIndex + 1;
    if (nextIndex < session.form.fields.length) {
      const updated = { ...session, values, fieldIndex: nextIndex, attempts: 0, phase: "asking" };
      formSessionRef.current = updated;
      setFormSession(updated);
      setManualFieldValue("");
      askField(session.form, nextIndex);
    } else {
      const updated = { ...session, values, phase: "confirming" };
      formSessionRef.current = updated;
      setFormSession(updated);
      setManualFieldValue("");
      speakConfirmationSummary(session.form, values);
    }
  };

  const handleFormFieldAnswer = (rawText) => {
    const session = formSessionRef.current;
    if (!session) return;
    const field = session.form.fields[session.fieldIndex];
    const result = validateFieldAnswer(field, rawText);

    if (!result.ok) {
      const attempts = session.attempts + 1;
      if (attempts >= 3) {
        const updated = { ...session, phase: "manual-entry", attempts };
        formSessionRef.current = updated;
        setFormSession(updated);
        const msg = `Let's type the answer for ${field.label} instead — use the box below.`;
        setTurns((prev) => [...prev, { role: "assistant", text: msg }]);
        speak(msg);
        return;
      }
      const updated = { ...session, attempts };
      formSessionRef.current = updated;
      setFormSession(updated);
      const options = Array.isArray(field.options) ? field.options : [];
      const hint = options.length ? ` You can say: ${options.join(", ")}.` : "";
      speak(`Sorry, I didn't get a valid answer for ${field.label}.${hint}`, { autoListen: true });
      return;
    }

    applyFieldValue(session, field, result.value);
  };

  const submitManualFieldValue = () => {
    const session = formSessionRef.current;
    if (!session) return;
    const field = session.form.fields[session.fieldIndex];
    const trimmed = manualFieldValue.trim();
    if (field.required && !trimmed) return;
    applyFieldValue(session, field, trimmed);
  };

  const speakConfirmationSummary = (form, values) => {
    const summary = form.fields
      .map((field) => {
        const value = values[field.name];
        const formatted = typeof value === "boolean" ? (value ? "yes" : "no") : value || "not provided";
        return `${field.label}: ${formatted}`;
      })
      .join(". ");
    const prompt = `Here's what I have — ${summary}. Should I submit this?`;
    setTurns((prev) => [...prev, { role: "assistant", text: prompt }]);
    speak(prompt, { autoListen: true });
  };

  const handleConfirmationAnswer = (rawText) => {
    const session = formSessionRef.current;
    if (!session) return;
    const yn = parseYesNo(rawText);
    if (yn === "yes") {
      submitFormSession(session);
      return;
    }
    if (yn === "no") {
      const updated = { ...session, phase: "redo-select" };
      formSessionRef.current = updated;
      setFormSession(updated);
      speak("Which field would you like to change?", { autoListen: true });
      return;
    }
    speak("Sorry, was that a yes or a no?", { autoListen: true });
  };

  const handleRedoSelection = (rawText) => {
    const session = formSessionRef.current;
    if (!session) return;
    const matchIndex = session.form.fields.findIndex((field) => matchesFieldLabel(rawText, field.label));
    if (matchIndex === -1) {
      speak("I didn't catch which field — could you say its name again?", { autoListen: true });
      return;
    }
    const updated = { ...session, fieldIndex: matchIndex, phase: "asking", attempts: 0 };
    formSessionRef.current = updated;
    setFormSession(updated);
    askField(session.form, matchIndex);
  };

  const submitFormSession = async (session) => {
    setAgentState("processing");
    try {
      const jwt = getToken();
      const response = await fetch(URL.FORM_SUBMISSIONS(agentId), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        },
        body: JSON.stringify({ form_id: session.form.id, values: session.values }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(apiErrorMessage(data, response.status));

      const confirmation = session.form.confirmationMessage || "Thanks — your form has been submitted.";
      formSessionRef.current = null;
      setFormSession(null);
      setTurns((prev) => [...prev, { role: "assistant", text: confirmation }]);
      speak(confirmation, { autoListen: true });
    } catch (err) {
      console.error("Voice form submit error:", err);
      reportError(err.message || "Could not submit the form. Please try again.");
    }
  };

  // --- Recording / transcription ----------------------------------------------

  const routeTranscript = (text) => {
    setTurns((prev) => [...prev, { role: "user", text }]);
    const session = formSessionRef.current;
    if (session) {
      if (session.phase === "asking") {
        handleFormFieldAnswer(text);
      } else if (session.phase === "confirming") {
        handleConfirmationAnswer(text);
      } else if (session.phase === "redo-select") {
        handleRedoSelection(text);
      }
      return Promise.resolve();
    }
    return sendVoiceMessage(text);
  };

  const transcribeRecording = async (sessionId, wavBlob) => {
    if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
    if (!wavBlob?.size) {
      logVoiceDiagnostic("recording_empty");
      resumeConversationListening();
      return;
    }

    const wavMetadata = await getWavMetadata(wavBlob).catch(() => null);
    logVoiceDiagnostic("recording_completed", {
      bytes: wavBlob.size,
      durationSeconds: wavMetadata?.durationSeconds ?? null,
      sampleRate: wavMetadata?.sampleRate ?? null,
      channels: wavMetadata?.channels ?? null,
      validWavHeader: Boolean(wavMetadata),
    });
    setAgentState("processing");
    transcriptionRequestControllerRef.current?.abort();
    const requestController = new AbortController();
    transcriptionRequestControllerRef.current = requestController;
    try {
      const formData = new FormData();
      formData.append("audio", wavBlob, "voice-input.wav");
      if (language !== "auto") formData.append("language", language);

      const jwt = getToken();
      logVoiceDiagnostic("stt_request_started");
      const response = await fetch(URL.VOICE_STT, {
        method: "POST",
        headers: {
          ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        },
        body: formData,
        signal: requestController.signal,
      });
      if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
      const data = await response.json().catch(() => ({}));
      logVoiceDiagnostic("stt_response", {
        status: response.status,
        errorCode: response.ok ? null : data.error || "unknown",
      });
      if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
      if (!response.ok) {
        if (response.status >= 500) setServiceDisconnected(true);
        if (response.status === 429) {
          const error = new Error(
            data.message || "Gemini transcription quota is temporarily unavailable."
          );
          error.status = response.status;
          error.quotaExceeded = true;
          throw error;
        }
        throw new Error(apiErrorMessage(data, response.status));
      }

      setServiceDisconnected(false);
      const heard = (data.text || "").trim();
      logVoiceDiagnostic("stt_succeeded", { transcriptPresent: Boolean(heard) });
      if (!heard) {
        resumeConversationListening();
        return;
      }
      void routeTranscript(heard);
    } catch (err) {
      if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
      console.error("Voice transcription error:", err);
      logVoiceDiagnostic("stt_failed", {
        status: err.status || null,
        errorType: err.name || "Error",
        quotaExceeded: Boolean(err.quotaExceeded),
      });
      if (err.quotaExceeded) {
        stopConversation();
        setError(err.message);
        setAgentState("stopped");
        return;
      }
      if (err instanceof TypeError) setServiceDisconnected(true);
      reportError(err.message || "I had trouble hearing that. I'll listen again in a moment.");
    } finally {
      if (transcriptionRequestControllerRef.current === requestController) {
        transcriptionRequestControllerRef.current = null;
      }
    }
  };

  const stopSilenceWatcher = () => {
    if (silenceRafRef.current !== null) {
      cancelAnimationFrame(silenceRafRef.current);
      silenceRafRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    if (recordingTimeoutRef.current !== null) {
      window.clearTimeout(recordingTimeoutRef.current);
      recordingTimeoutRef.current = null;
    }
    hasSpokenRef.current = false;
    voiceActivityDetectorRef.current = null;
    recordingStartedAtRef.current = null;
  };

  const startSilenceWatcher = (stream) => {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return false;

    try {
      const audioContext = new AudioContextCtor();
      const source = audioContext.createMediaStreamSource(stream);
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      audioContextRef.current = audioContext;
      if (audioContext.state === "suspended") {
        audioContext.resume().catch((err) => console.warn("Audio level monitoring could not start:", err));
      }
      hasSpokenRef.current = false;
      voiceActivityDetectorRef.current = createVoiceActivityDetector({
        threshold: SPEECH_RMS_THRESHOLD,
        speechStartMs: SPEECH_START_MS,
        silenceEndMs: SILENCE_STOP_MS,
      });

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(dataArray);
        const volume = computeRms(dataArray);
        const now = Date.now();
        const activity = voiceActivityDetectorRef.current?.update(volume, now);

        if (activity === "speech-start") {
          logVoiceDiagnostic("speech_detected");
          hasSpokenRef.current = true;
          setAgentState("recording");
          if (recordingTimeoutRef.current !== null) {
            window.clearTimeout(recordingTimeoutRef.current);
          }
          const elapsed = now - recordingStartedAtRef.current;
          recordingTimeoutRef.current = window.setTimeout(
            stopRecording,
            Math.max(0, MAX_RECORDING_MS - elapsed)
          );
        } else if (activity === "speech-end") {
          logVoiceDiagnostic("speech_ended");
          stopRecording();
          return;
        }

        silenceRafRef.current = requestAnimationFrame(tick);
      };

      silenceRafRef.current = requestAnimationFrame(tick);
      return true;
    } catch (err) {
      console.warn("Automatic silence detection is unavailable:", err);
      return false;
    }
  };

  const startRecording = async () => {
    const sessionId = voiceSessionIdRef.current;
    if (recordingSetupRef.current || pcmRecorderRef.current) return;
    if (isAgentExpired(agentRef.current)) return;
    if (!micSupported) {
      setConversationMode(false);
      reportError("Voice input isn't supported in this browser.", { resumeListening: false });
      return;
    }

    recordingSetupRef.current = true;
    const existingStream = streamRef.current;
    const hasLiveAudioTrack = existingStream?.getAudioTracks().some((track) => track.readyState === "live");
    setAgentState(hasLiveAudioTrack ? "listening" : "requesting_permission");
    audioPlayerRef.current?.pause?.();
    setError("");

    let acquiredStream = null;
    try {
      const stream = hasLiveAudioTrack
        ? existingStream
        : await navigator.mediaDevices.getUserMedia({ audio: true });
      acquiredStream = stream;
      if (!continuousSessionRef.current || sessionId !== voiceSessionIdRef.current) {
        if (!hasLiveAudioTrack) stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      logVoiceDiagnostic("microphone_stream_ready", {
        audioTrackLive: stream.getAudioTracks().some((track) => track.readyState === "live"),
      });
      const pcmRecorder = await createPcmWavRecorder(stream);
      if (!continuousSessionRef.current || sessionId !== voiceSessionIdRef.current) {
        await pcmRecorder.stop();
        return;
      }
      pcmRecorderRef.current = pcmRecorder;
      pcmRecorderSessionRef.current = sessionId;
      if (!startSilenceWatcher(stream)) {
        throw new Error("Automatic speech detection is unavailable in this browser.");
      }
      logVoiceDiagnostic("recording_started");
      recordingSetupRef.current = false;
      setAgentState("listening");
      recordingStartedAtRef.current = Date.now();
      recordingTimeoutRef.current = window.setTimeout(stopRecording, NO_SPEECH_STOP_MS);
    } catch (err) {
      if (sessionId === voiceSessionIdRef.current) recordingSetupRef.current = false;
      stopSilenceWatcher();
      if (pcmRecorderRef.current && pcmRecorderSessionRef.current === sessionId) {
        const recorder = pcmRecorderRef.current;
        pcmRecorderRef.current = null;
        pcmRecorderSessionRef.current = null;
        try {
          await recorder.stop();
        } catch (cleanupError) {
          console.warn("Microphone recorder cleanup failed:", cleanupError);
        }
      }
      acquiredStream?.getTracks().forEach((track) => track.stop());
      if (streamRef.current === acquiredStream) streamRef.current = null;
      if (sessionId !== voiceSessionIdRef.current || !continuousSessionRef.current) return;
      console.error("Microphone access failed:", err);
      logVoiceDiagnostic("recording_failed", { errorType: err.name || "Error" });
      setConversationMode(false);
      voiceSessionIdRef.current += 1;
      reportError(err.message || "Microphone access is unavailable. Check your browser permissions and try again.", {
        resumeListening: false,
      });
    }
  };

  // Stops the recording - called either automatically once the user falls
  // silent after speaking, or manually via the mic button if someone wants to
  // cut a recording short.
  const stopRecording = () => {
    const speechDetected = hasSpokenRef.current;
    const recorder = pcmRecorderRef.current;
    const sessionId = pcmRecorderSessionRef.current;
    pcmRecorderRef.current = null;
    pcmRecorderSessionRef.current = null;
    recordingSetupRef.current = false;
    stopSilenceWatcher();
    if (!recorder) return;
    if (!speechDetected) {
      void recorder.stop().catch((err) => console.warn("Silent microphone capture cleanup failed:", err));
      if (continuousSessionRef.current) resumeConversationListening();
      return;
    }
    setAgentState("processing");
    void recorder
      .stop()
      .then((wavBlob) => transcribeRecording(sessionId, wavBlob))
      .catch((err) => {
        if (!continuousSessionRef.current) return;
        console.error("Microphone audio capture failed:", err);
        logVoiceDiagnostic("recording_failed", { errorType: err.name || "Error" });
        reportError("I couldn't prepare that recording. I'll listen again in a moment.");
      });
  };

  const interruptSpeaking = ({ updateUi = true } = {}) => {
    speechActiveRef.current = false;
    const audioEl = audioPlayerRef.current;
    if (audioEl) {
      audioEl.onended = null;
      audioEl.onerror = null;
    }
    stopAudioPlayback(audioEl, audioObjectUrlRef.current);
    audioObjectUrlRef.current = null;
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    stopSpeechAnalyser();
    if (updateUi) setAgentState("idle");
  };

  const stopConversation = ({ updateUi = true } = {}) => {
    setConversationMode(false, updateUi);
    voiceSessionIdRef.current += 1;
    recordingSetupRef.current = false;
    chatRequestControllerRef.current?.abort();
    chatRequestControllerRef.current = null;
    transcriptionRequestControllerRef.current?.abort();
    transcriptionRequestControllerRef.current = null;
    speechRequestControllerRef.current?.abort();
    speechRequestControllerRef.current = null;

    const pcmRecorder = pcmRecorderRef.current;
    pcmRecorderRef.current = null;
    pcmRecorderSessionRef.current = null;
    if (pcmRecorder) {
      void pcmRecorder.stop().catch((err) => console.warn("Microphone recorder cleanup failed:", err));
    }

    stopSilenceWatcher();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    interruptSpeaking({ updateUi });
    if (updateUi) setAgentState("stopped");
  };

  stopConversationRef.current = stopConversation;

  const handlePrimaryButton = () => {
    if (continuousSessionRef.current) {
      stopConversation();
      return;
    }

    setConversationMode(true);
    voiceSessionIdRef.current += 1;
    setError("");
    if (audioPlayerRef.current) void prepareSpeechAnalyser(audioPlayerRef.current);
    void startRecording();
  };

  const expired = isAgentExpired(agent);
  const lastAssistantTurn = [...turns].reverse().find((t) => t.role === "assistant");
  const isGreeting = /^(hello|hi|hey|good morning|good afternoon|good evening)\b/i.test(lastAssistantTurn?.text || "");
  const isManualEntry = formSession?.phase === "manual-entry";
  const avatarState = serviceDisconnected
    ? "disconnected"
    : error
    ? "error"
    : !micSupported
    ? "disconnected"
    : agentState === "listening" || agentState === "recording"
    ? "recording"
    : agentState === "requesting_permission" || agentState === "processing" || agentState === "recovering"
    ? "thinking"
    : agentState === "stopped" || agentState === "error"
    ? "idle"
    : agentState;

  const statusMsg = expired
    ? "This agent's subscription has expired"
    : !micSupported
    ? "Voice input isn't supported in this browser"
    : serviceDisconnected
    ? "Voice service is disconnected"
    : error
    ? "Something went wrong — please try again"
    : isManualEntry
    ? "Type your answer below"
    : {
        idle: "Start a voice conversation",
        requesting_permission: "Requesting microphone access...",
        listening: "Listening...",
        recording: "Listening...",
        processing: "Thinking...",
        speaking: "Speaking — tap Stop to end the conversation",
        recovering: "Reconnecting...",
        stopped: "Conversation stopped",
        error: "Voice conversation needs attention",
      }[agentState];

  return (
    <AgentChatShell>
      <div className="voice-agent-chat">
        <audio ref={audioPlayerRef} style={{ display: "none" }} />

        {expired && (
          <div className="voice-agent-expired-banner">
            ⚠️ Expired Subscription.{" "}
            {userRole === "admin"
              ? "Please return to the dashboard to reactivate this agent."
              : "Please contact your administrator to reactivate it."}
          </div>
        )}

        <div className="voice-agent-stage-wrap">
          <AvatarStage state={avatarState} statusMsg={statusMsg} audioLevelRef={speechAudioLevelRef} greeting={isGreeting} />

          <div className="voice-agent-language-picker">
            {Object.values(VOICE_LANGUAGES).map((lang) => (
              <button
                key={lang.code}
                type="button"
                className={`voice-agent-language-btn${language === lang.code ? " active" : ""}`}
                onClick={() => setLanguage(lang.code)}
                disabled={agentState === "recording" || agentState === "processing"}
                title={lang.nativeLabel}
                aria-label={`Speech language: ${lang.nativeLabel}`}
              >
                {lang.flag}
              </button>
            ))}
          </div>

          <button
            type="button"
            className="voice-agent-playback-toggle"
            onClick={() => {
              if (speechActiveRef.current) {
                speechRequestControllerRef.current?.abort();
                interruptSpeaking();
                if (continuousSessionRef.current) resumeConversationListening();
              } else if (lastAssistantTurn?.text) {
                setError("");
                void speak(lastAssistantTurn.text);
              }
            }}
            title={speechActiveRef.current ? "Stop speech playback" : "Play the latest response"}
            aria-label={speechActiveRef.current ? "Stop speech playback" : "Play the latest response"}
            disabled={!lastAssistantTurn?.text || (agentState === "processing" && !speechActiveRef.current)}
          >
            {speechActiveRef.current ? <FiVolumeX /> : <FiVolume2 />}
          </button>

        </div>

        <div className="voice-agent-controls">
          {error && <div className="voice-agent-error">⚠️ {error}</div>}
          {!isManualEntry && micSupported && !expired && (
            <div className="voice-agent-help" role="status" aria-live="polite">
              {statusMsg}
            </div>
          )}

          {isManualEntry && (
            <div className="voice-agent-manual-entry">
              <input
                type="text"
                value={manualFieldValue}
                onChange={(event) => setManualFieldValue(event.target.value)}
                placeholder="Type your answer..."
                autoFocus
                onKeyDown={(event) => {
                  if (event.key === "Enter") submitManualFieldValue();
                }}
              />
              <button type="button" onClick={submitManualFieldValue}>
                Continue
              </button>
            </div>
          )}

          <button
            type="button"
            className={`voice-agent-mic-btn voice-agent-mic-btn--${agentState}`}
            onClick={handlePrimaryButton}
            aria-label={
              conversationActive
                ? "Stop the voice conversation"
                : "Start the voice conversation"
            }
            aria-pressed={conversationActive}
            disabled={expired || !micSupported || (isManualEntry && !continuousSessionRef.current)}
          >
            {conversationActive ? <FiSquare /> : <FiMic />}
            <span>
              {conversationActive ? "Stop Conversation" : "Start Conversation"}
            </span>
          </button>
        </div>
      </div>
    </AgentChatShell>
  );
};

export default VoiceAgentChat;
