import { fetchTtsWithRetry, quotaErrorMessage } from "./voiceRequests";

describe("voice TTS request recovery", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  test("retries one 429 response immediately when Retry-After is zero", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({ status: 429, headers: { get: () => "0" } })
      .mockResolvedValueOnce({ status: 200 });

    await expect(fetchTtsWithRetry("/tts", { signal: new AbortController().signal })).resolves.toMatchObject({
      status: 200,
    });
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  test("does not retry beyond the configured bound", async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue({ status: 429, headers: { get: () => "0" } });

    const response = await fetchTtsWithRetry("/tts", { signal: new AbortController().signal });

    expect(response.status).toBe(429);
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  test("does not exceed the bounded delay when Retry-After is too long", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      status: 429,
      headers: { get: () => "60" },
    });

    const response = await fetchTtsWithRetry("/tts", { signal: new AbortController().signal });

    expect(response.status).toBe(429);
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  test("explains long Retry-After quota waits in user-facing terms", () => {
    const now = Date.now();
    jest.spyOn(Date, "now").mockReturnValue(now);

    expect(
      quotaErrorMessage({ message: "Daily Gemini quota reached." }, "57315")
    ).toBe("Daily Gemini quota reached. Retry in about 15 hours and 55 minutes.");

    Date.now.mockRestore();
  });
});
