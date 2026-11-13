const MAX_RETRIES = 1;
const MAX_RETRY_DELAY_MS = 10000;

export const quotaErrorMessage = (data, retryAfter) => {
  const message = data?.message || "Gemini voice usage is temporarily limited.";
  if (!retryAfter) return message;

  const seconds = Number(retryAfter);
  const retryAt = Number.isFinite(seconds)
    ? Date.now() + seconds * 1000
    : new Date(retryAfter).getTime();
  const remainingSeconds = Math.max(0, Math.floor((retryAt - Date.now()) / 1000));
  if (!Number.isFinite(retryAt) || remainingSeconds === 0) return message;

  const hours = Math.floor(remainingSeconds / 3600);
  const minutes = Math.floor((remainingSeconds % 3600) / 60);
  const wait = [
    hours ? `${hours} hour${hours === 1 ? "" : "s"}` : "",
    minutes ? `${minutes} minute${minutes === 1 ? "" : "s"}` : "",
  ].filter(Boolean).join(" and ");
  return wait ? `${message} Retry in about ${wait}.` : `${message} Retry in less than a minute.`;
};

const retryDelayMs = (retryAfter) => {
  if (!retryAfter) return 1000;
  const seconds = Number(retryAfter);
  const delay = Number.isFinite(seconds)
    ? seconds * 1000
    : new Date(retryAfter).getTime() - Date.now();
  return delay >= 0 && delay <= MAX_RETRY_DELAY_MS ? delay : null;
};

const waitForRetry = (delay, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("The operation was aborted.", "AbortError"));
      return;
    }
    if (delay === 0) {
      resolve();
      return;
    }
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delay);
    const onAbort = () => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });

export const fetchTtsWithRetry = async (url, options) => {
  for (let attempt = 0; ; attempt += 1) {
    const response = await fetch(url, options);
    if (response.status !== 429 || attempt >= MAX_RETRIES) return response;
    const delay = retryDelayMs(response.headers?.get?.("Retry-After"));
    if (delay === null) return response;
    await waitForRetry(delay, options.signal);
  }
};
