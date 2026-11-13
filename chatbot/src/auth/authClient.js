import { URL } from "../urls";

const TOKEN_KEY = "agentstore_token";
const AUTH_CHANGED_EVENT = "agentstore-auth-changed";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.clear(); // Clear MSAL session state robustly
  window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
}

export function onAuthChanged(handler) {
  window.addEventListener(AUTH_CHANGED_EVENT, handler);
  return () => window.removeEventListener(AUTH_CHANGED_EVENT, handler);
}

export async function fetchMe() {
  const token = getToken();
  if (!token) return null;

  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 15000);
  let res;
  try {
    res = await fetch(URL.AUTH_ME, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }

  if (!res.ok) {
    clearToken();
    return null;
  }

  return await res.json();
}

export async function authLogin({ email, password }) {
  const res = await fetch(URL.AUTH_LOGIN, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

export async function authAzureLogin(accessToken) {
  const res = await fetch(URL.AUTH_AZURE_LOGIN, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken }),
  });

  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

export async function authSignup(payload) {
  const res = await fetch(URL.AUTH_SIGNUP, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

