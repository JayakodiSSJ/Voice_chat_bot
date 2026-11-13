import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import WhatsAppConfiguration from "./WhatsAppConfiguration";

jest.mock("./AppDialog", () => ({
  useAppDialog: () => ({ showConfirm: () => Promise.resolve(true) }),
}));

const getAuthHeaders = () => ({ Authorization: "Bearer test-token" });

beforeEach(() => {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    json: async () => ({}),
  });
});

afterEach(() => {
  jest.restoreAllMocks();
});

test("shows the persisted WhatsApp connection states when the add-on is enabled", async () => {
  global.fetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      entitlement: { enabled: true },
      connection: { configured: false, phone_number: null },
    }),
  });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  expect(screen.getByRole("dialog", { name: "WhatsApp Configuration" })).toBeInTheDocument();
  expect(screen.getByText("WhatsApp add-on active")).toBeInTheDocument();
  expect(screen.getByText("Meta business account")).toBeInTheDocument();
  expect(screen.getByText("WhatsApp number")).toBeInTheDocument();
  expect(await screen.findByText("Not configured")).toBeInTheDocument();
  expect(screen.getByText("App secret required")).toBeInTheDocument();
  expect(screen.getByText("Credentials required")).toBeInTheDocument();
});

test("shows the monthly offer when the company does not have the add-on", async () => {
  global.fetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      entitlement: { enabled: false },
      addon: {
        offer: {
          monthly_price_usd: 19.99,
          currency: "usd",
          available: true,
        },
        subscription: { status: "not_purchased" },
        can_purchase: true,
      },
    }),
  });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: false }}
    />
  );

  expect(screen.getByText("WhatsApp add-on required")).toBeInTheDocument();
  expect(await screen.findByText("$19.99")).toBeInTheDocument();
  expect(screen.getByText("Shared workspace token quota")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Purchase add-on" })).toBeInTheDocument();
  expect(screen.queryByText("Meta business account")).not.toBeInTheDocument();
  expect(global.fetch).toHaveBeenCalledWith(
    "/api/auth/whatsapp/configuration",
    expect.objectContaining({ headers: expect.any(Object) })
  );
});

test("starts Stripe Checkout from the locked configuration window", async () => {
  const onCheckoutRedirect = jest.fn();
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entitlement: { enabled: false },
        addon: {
          offer: {
            monthly_price_usd: 19.99,
            currency: "usd",
            available: true,
          },
          subscription: { status: "not_purchased" },
          can_purchase: true,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        url: "https://checkout.stripe.test/cs_whatsapp",
        subscription: { status: "checkout_pending" },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: false }}
      getAuthHeaders={getAuthHeaders}
      onCheckoutRedirect={onCheckoutRedirect}
    />
  );

  fireEvent.click(await screen.findByRole("button", { name: "Purchase add-on" }));

  await waitFor(() => {
    expect(global.fetch).toHaveBeenLastCalledWith(
      "/api/auth/whatsapp/addon/checkout",
      expect.objectContaining({ method: "POST" })
    );
  });
  expect(onCheckoutRedirect).toHaveBeenCalledWith(
    "https://checkout.stripe.test/cs_whatsapp"
  );
});

test("keeps the purchase option available when Stripe checkout fails", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entitlement: { enabled: false },
        addon: {
          offer: {
            monthly_price_usd: 19.99,
            currency: "usd",
            available: true,
          },
          subscription: { status: "not_purchased" },
          can_purchase: true,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: false,
      json: async () => ({ message: "Stripe is temporarily unavailable." }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: false }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  fireEvent.click(await screen.findByRole("button", { name: "Purchase add-on" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Stripe is temporarily unavailable."
  );
  expect(screen.getByRole("button", { name: "Purchase add-on" })).toBeEnabled();
});

test("schedules cancellation and restores renewal for an active add-on", async () => {
  jest.spyOn(window, "confirm").mockReturnValue(true);
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entitlement: { enabled: true },
        connection: { configured: false, phone_number: null },
        addon: {
          subscription: {
            status: "active",
            cancel_at_period_end: false,
            current_period_end: "2026-09-01T00:00:00+00:00",
          },
          can_cancel: true,
          can_reactivate: false,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        message: "WhatsApp will remain active until the current billing period ends.",
        entitlement: { enabled: true },
        addon: {
          subscription: {
            status: "cancel_at_period_end",
            cancel_at_period_end: true,
            current_period_end: "2026-09-01T00:00:00+00:00",
          },
          can_cancel: false,
          can_reactivate: true,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        message: "WhatsApp monthly renewal has been restored.",
        entitlement: { enabled: true },
        addon: {
          subscription: {
            status: "active",
            cancel_at_period_end: false,
            current_period_end: "2026-09-01T00:00:00+00:00",
          },
          can_cancel: true,
          can_reactivate: false,
        },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  fireEvent.click(await screen.findByRole("button", { name: "Cancel renewal" }));

  await waitFor(() => {
    expect(global.fetch).toHaveBeenLastCalledWith(
      "/api/auth/whatsapp/addon/subscription",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "cancel" }),
      })
    );
  });
  expect(await screen.findByText("WhatsApp cancellation scheduled")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Restore renewal" }));

  await waitFor(() => {
    expect(global.fetch).toHaveBeenLastCalledWith(
      "/api/auth/whatsapp/addon/subscription",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "reactivate" }),
      })
    );
  });
  expect(await screen.findByText("WhatsApp monthly renewal has been restored.")).toBeInTheDocument();
  expect(screen.getByText("WhatsApp add-on active")).toBeInTheDocument();
});

test("closes from the icon button", async () => {
  const onClose = jest.fn();
  render(
    <WhatsAppConfiguration
      open
      onClose={onClose}
      entitlement={{ enabled: false }}
    />
  );

  await screen.findByText("Purchasing is currently unavailable.");
  fireEvent.click(screen.getByRole("button", { name: "Close WhatsApp configuration" }));
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("saves and displays a normalized WhatsApp number", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ connection: { configured: false, phone_number: null } }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: { configured: true, phone_number: "+94771234567" },
        webhook: {
          callback_url: "https://api.example.com/api/whatsapp/webhook/key",
          verify_token: "verify-token",
          app_secret_configured: false,
          verified_at: null,
        },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  const input = await screen.findByLabelText("WhatsApp business number");
  await waitFor(() => expect(input).not.toBeDisabled());
  fireEvent.change(input, { target: { value: "+94 77 123 4567" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));

  expect(await screen.findByText("WhatsApp number saved.")).toBeInTheDocument();
  expect(input).toHaveValue("+94771234567");
  expect(screen.getByLabelText("Callback URL")).toHaveValue(
    "https://api.example.com/api/whatsapp/webhook/key"
  );
  expect(screen.getByLabelText("Verify token")).toHaveValue("verify-token");
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/auth/whatsapp/configuration",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ phone_number: "+94 77 123 4567", provider: "meta" }),
    })
  );
});

test("shows a duplicate-number error returned by the API", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ connection: { configured: false, phone_number: null } }),
    })
    .mockResolvedValueOnce({
      ok: false,
      json: async () => ({
        error: "phone_number_in_use",
        message: "This WhatsApp number is already assigned to another company.",
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  const input = await screen.findByLabelText("WhatsApp business number");
  await waitFor(() => expect(input).not.toBeDisabled());
  fireEvent.change(input, { target: { value: "+94771234567" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));

  expect(
    await screen.findByRole("alert")
  ).toHaveTextContent("This WhatsApp number is already assigned to another company.");
});

test("sends a newly entered Meta app secret without redisplaying it", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: { configured: true, phone_number: "+94771234567" },
        webhook: {
          callback_url: "https://api.example.com/api/whatsapp/webhook/key",
          verify_token: "verify-token",
          app_secret_configured: false,
          verified_at: null,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: { configured: true, phone_number: "+94771234567" },
        webhook: {
          callback_url: "https://api.example.com/api/whatsapp/webhook/key",
          verify_token: "verify-token",
          app_secret_configured: true,
          verified_at: null,
        },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  const secretInput = await screen.findByLabelText("Meta app secret");
  await waitFor(() => expect(secretInput).not.toBeDisabled());
  fireEvent.change(secretInput, { target: { value: "meta-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));

  await waitFor(() => expect(secretInput).toHaveValue(""));
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/auth/whatsapp/configuration",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        phone_number: "+94771234567",
        provider: "meta",
        meta_app_secret: "meta-secret",
      }),
    })
  );
  expect(screen.getByText("Awaiting verification")).toBeInTheDocument();
});

test("saves outbound Meta credentials without redisplaying the access token", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: {
          configured: true,
          phone_number: "+94771234567",
          meta_phone_number_id: null,
          access_token_configured: false,
          outbound_ready: false,
        },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: {
          configured: true,
          phone_number: "+94771234567",
          meta_phone_number_id: "123456789012345",
          access_token_configured: true,
          outbound_ready: true,
        },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  const phoneIdInput = await screen.findByLabelText("Meta Phone Number ID");
  const accessTokenInput = screen.getByLabelText("Meta system-user access token");
  await waitFor(() => expect(phoneIdInput).not.toBeDisabled());
  fireEvent.change(phoneIdInput, { target: { value: "123456789012345" } });
  fireEvent.change(accessTokenInput, { target: { value: "system-user-token" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));

  await waitFor(() => expect(accessTokenInput).toHaveValue(""));
  expect(phoneIdInput).toHaveValue("123456789012345");
  expect(screen.getByText("Configured")).toBeInTheDocument();
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/auth/whatsapp/configuration",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        phone_number: "+94771234567",
        provider: "meta",
        meta_phone_number_id: "123456789012345",
        meta_access_token: "system-user-token",
      }),
    })
  );
});

test("configures WAHA without exposing its API key", async () => {
  global.fetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        entitlement: { enabled: true },
        connection: { configured: false, provider: "meta", phone_number: null },
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        connection: {
          configured: true,
          provider: "waha",
          phone_number: "+94771234567",
          waha_session: "workspace-7",
          waha_api_key_configured: true,
          outbound_ready: true,
        },
        webhook: {
          provider: "waha",
          callback_url: "https://api.example.com/api/whatsapp/webhook/key",
          hmac_secret: "waha-hmac-secret",
          verified_at: null,
        },
      }),
    });

  render(
    <WhatsAppConfiguration
      open
      onClose={() => {}}
      entitlement={{ enabled: true }}
      getAuthHeaders={getAuthHeaders}
    />
  );

  const providerSelect = await screen.findByLabelText("Messaging provider");
  await waitFor(() => expect(providerSelect).not.toBeDisabled());
  fireEvent.change(providerSelect, { target: { value: "waha" } });
  fireEvent.change(screen.getByLabelText("WhatsApp business number"), {
    target: { value: "+94771234567" },
  });
  fireEvent.change(screen.getByLabelText("WAHA session"), {
    target: { value: "workspace-7" },
  });
  const apiKeyInput = screen.getByLabelText("WAHA API key");
  fireEvent.change(apiKeyInput, { target: { value: "waha-api-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save setup" }));

  await waitFor(() => expect(apiKeyInput).toHaveValue(""));
  expect(screen.getByText("WAHA webhook")).toBeInTheDocument();
  expect(screen.getByLabelText("HMAC secret")).toHaveValue("waha-hmac-secret");
  expect(global.fetch).toHaveBeenLastCalledWith(
    "/api/auth/whatsapp/configuration",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        phone_number: "+94771234567",
        provider: "waha",
        waha_session: "workspace-7",
        waha_api_key: "waha-api-key",
      }),
    })
  );
});
