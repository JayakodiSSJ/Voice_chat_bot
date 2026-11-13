
export const BASE_URL = process.env.REACT_APP_API_URL || "/api";

export const URL = {
    GET_TOKEN_DIGITAL: `${BASE_URL}/get_token_digital`,
    GET_TOKEN_EMBRYO: `${BASE_URL}/get_token_embryo`,
    GET_TOKEN_INNOSPACE: `${BASE_URL}/get_token_innospace`,
    GET_TOKEN_PLACEMENT: `${BASE_URL}/get_token_placement`,
    GET_TOKEN_TEST: `${BASE_URL}/get_token_test`,
    GET_TOKEN_SLTMNIT: `${BASE_URL}/get_token_sltmnit`,


    CHAT_EKB: `${BASE_URL}/chat_ekb`,
    CHAT: `${BASE_URL}/auth/chat`,
    VOICE_READY: `${BASE_URL}/auth/voice/ready`,
    VOICE_STT: `${BASE_URL}/auth/voice/stt`,
    VOICE_TTS: `${BASE_URL}/auth/voice/tts`,

    AUTH_LOGIN: `${BASE_URL}/auth/login`,
    AUTH_AZURE_LOGIN: `${BASE_URL}/auth/azure-login`,
    AUTH_SIGNUP: `${BASE_URL}/auth/signup`,
    AUTH_ME: `${BASE_URL}/auth/me`,
    AUTH_RESET_PASSWORD_VERIFY: `${BASE_URL}/auth/reset-password/verify`,
    AUTH_RESET_PASSWORD: `${BASE_URL}/auth/reset-password`,
    AUTH_USERS: `${BASE_URL}/auth/users`,
    AUTH_USER_INVITATIONS: `${BASE_URL}/auth/user-invitations`,
    LOGIN_REQUESTS: `${BASE_URL}/auth/login-requests`,
    COMPANY_REGISTRATIONS: `${BASE_URL}/auth/company-registrations`,
    RESET_DATABASE: `${BASE_URL}/auth/reset-database`,

    COMPANIES: `${BASE_URL}/auth/companies`,
    ADMIN_PAYMENTS: `${BASE_URL}/auth/admin/payments`,
    SUBSCRIPTION_PACKAGES: `${BASE_URL}/subscription-packages`,
    AUTH_SUBSCRIPTION_PACKAGES: `${BASE_URL}/auth/subscription-packages`,
    ADMIN_SUBSCRIPTION_PACKAGES: `${BASE_URL}/auth/admin/subscription-packages`,
    ADMIN_WHATSAPP_ADDON: `${BASE_URL}/auth/admin/features/whatsapp/addon`,
    CUSTOM_AGENTS: `${BASE_URL}/auth/agent-configurations`,
    AGENT_CONFIGURATIONS: `${BASE_URL}/auth/agent-configurations`,
    AGENT_CONFIGURATION: (id) => `${BASE_URL}/auth/agent-configurations/${id}`,
    AGENT_PROVISIONING: (id) => `${BASE_URL}/auth/agent-configurations/${id}/provisioning`,
    CHAT_SESSIONS: (id) => `${BASE_URL}/auth/agent-configurations/${id}/chat-sessions`,
    CHAT_SESSION: (id, sessionId) => `${BASE_URL}/auth/agent-configurations/${id}/chat-sessions/${sessionId}`,
    CHAT_HISTORY: (id, sessionId) => {
        const base = `${BASE_URL}/auth/agent-configurations/${id}/chat-history`;
        return sessionId ? `${base}?session_id=${encodeURIComponent(sessionId)}` : base;
    },
    CHAT_USAGE: `${BASE_URL}/auth/chat-usage`,
    USER_CHAT_QUOTA: (userId) => `${BASE_URL}/auth/chat-usage/users/${userId}/quota`,
    WHATSAPP_CONFIGURATION: `${BASE_URL}/auth/whatsapp/configuration`,
    WHATSAPP_ADDON_CHECKOUT: `${BASE_URL}/auth/whatsapp/addon/checkout`,
    WHATSAPP_ADDON_PAYMENT_SUCCESS: `${BASE_URL}/auth/whatsapp/addon/payment-success`,
    WHATSAPP_ADDON_SUBSCRIPTION: `${BASE_URL}/auth/whatsapp/addon/subscription`,
    DATABASE_TOOL_AUDIT: `${BASE_URL}/auth/database-tool/audit`,
    FORM_SUBMISSIONS: (id) => `${BASE_URL}/auth/agent-configurations/${id}/form-submissions`,
    API_TOOL_VERIFY: `${BASE_URL}/auth/api-tool/verify`,
    TOOLS: `${BASE_URL}/auth/tools`,
    AGENT_RESOURCES: `${BASE_URL}/auth/agent-resources`,
    INGEST_URL: `${BASE_URL}/auth/ingest-url`,
    INGEST_ONEDRIVE: `${BASE_URL}/auth/ingest-onedrive`,
    INGEST_SHAREPOINT: `${BASE_URL}/auth/ingest-sharepoint`,
    INGESTION_STATUS: `${BASE_URL}/auth/ingestion-status`,
    CONTACT_SUBMIT: `${BASE_URL}/send-email`,
    CONTACT_US: `${BASE_URL}/send-email`,

    PING: `${BASE_URL}/ping`,
}
