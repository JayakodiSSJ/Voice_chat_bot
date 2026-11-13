import json
import re
from datetime import datetime, timezone

from db_compat import connect_postgres
from tool_observability import tool_data_redactor


EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# Intentionally not exhaustive; just enough to reliably detect common public providers.
PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "zoho.com",
    "gmx.com",
    "yandex.com",
    "mail.com",
}

# Every Gunicorn worker imports the application and calls init_db(). PostgreSQL
# permits concurrent DDL, but overlapping CREATE INDEX and backfill statements
# can acquire table locks in different orders and deadlock during worker boot.
# A transaction-scoped advisory lock makes database initialization a single-file
# operation across workers and even across application instances.
DATABASE_INIT_ADVISORY_LOCK = (1095975759, 1)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_email_domain(email: str) -> str:
    email = normalize_email(email)
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].strip().lower()


def is_valid_email(email: str) -> bool:
    email = normalize_email(email)
    return bool(email) and bool(EMAIL_REGEX.match(email))


def is_public_email_domain(domain: str) -> bool:
    return (domain or "").strip().lower() in PUBLIC_EMAIL_DOMAINS


def get_conn():
    return connect_postgres()


def _acquire_database_init_lock(conn) -> None:
    cursor = conn.execute(
        "SELECT pg_advisory_xact_lock(?, ?)",
        DATABASE_INIT_ADVISORY_LOCK,
    )
    cursor.close()


def calendar_month_period(at_time: str | None = None) -> tuple[str, str]:
    if at_time:
        current = datetime.fromisoformat(str(at_time).replace("Z", "+00:00"))
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
    else:
        current = datetime.now(timezone.utc)
    period_start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if period_start.month == 12:
        period_end = period_start.replace(year=period_start.year + 1, month=1)
    else:
        period_end = period_start.replace(month=period_start.month + 1)
    return period_start.isoformat(), period_end.isoformat()


def migrate_legacy_whatsapp_entitlements(
    conn,
    *,
    at_time: str | None = None,
) -> dict[str, int]:
    config = conn.execute(
        """
        SELECT monthly_quota
        FROM feature_addon_configs
        WHERE feature_key = 'whatsapp'
        LIMIT 1
        """
    ).fetchone()
    quota = int((config and config["monthly_quota"]) or 0)
    if quota <= 0:
        return {"subscriptions_created": 0, "usage_periods_created": 0}

    period_start, period_end = calendar_month_period(at_time)
    now = at_time or utcnow_iso()
    subscriptions = conn.execute(
        """
        INSERT OR IGNORE INTO company_feature_subscriptions (
            company_id,
            feature_key,
            status,
            billing_source,
            monthly_price_cents,
            currency,
            monthly_quota,
            current_period_start,
            current_period_end,
            cancel_at_period_end,
            stripe_event_created_at,
            created_at,
            updated_at
        )
        SELECT
            entitlement.company_id,
            'whatsapp',
            'active',
            'legacy',
            0,
            'usd',
            ?,
            ?,
            ?,
            0,
            0,
            ?,
            ?
        FROM company_feature_entitlements entitlement
        WHERE entitlement.feature_key = 'whatsapp'
          AND entitlement.is_enabled = 1
          AND NOT EXISTS (
              SELECT 1
              FROM company_feature_subscriptions subscription
              WHERE subscription.company_id = entitlement.company_id
                AND subscription.feature_key = 'whatsapp'
          )
        """,
        (quota, period_start, period_end, now, now),
    )
    periods = conn.execute(
        """
        INSERT OR IGNORE INTO company_feature_usage_periods (
            company_id,
            feature_key,
            feature_subscription_id,
            period_start,
            period_end,
            quota_limit,
            units_used,
            created_at,
            updated_at
        )
        SELECT
            subscription.company_id,
            'whatsapp',
            subscription.id,
            ?,
            ?,
            subscription.monthly_quota,
            0,
            ?,
            ?
        FROM company_feature_subscriptions subscription
        WHERE subscription.feature_key = 'whatsapp'
          AND subscription.billing_source = 'legacy'
          AND subscription.current_period_start = ?
        """,
        (period_start, period_end, now, now, period_start),
    )
    return {
        "subscriptions_created": max(0, int(subscriptions.rowcount or 0)),
        "usage_periods_created": max(0, int(periods.rowcount or 0)),
    }


def init_db() -> None:
    with get_conn() as conn:
        _acquire_database_init_lock(conn)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS company (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              domain TEXT UNIQUE,
              website TEXT,
              industry TEXT,
              allowed_agents TEXT,
              subscription_tier TEXT DEFAULT 'free',
              subscription_status TEXT DEFAULT 'inactive',
              subscription_expires_at TEXT,
              onboarding_completed INTEGER DEFAULT 0,
              onboarding_seen_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              full_name TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('admin','member')),
              company_id INTEGER NOT NULL,
              is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
              must_reset_password INTEGER NOT NULL DEFAULT 0 CHECK (must_reset_password IN (0,1)),
              created_at TEXT NOT NULL,
              updated_at TEXT,
              FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS login_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            company_id INTEGER NOT NULL,
            requested_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'approved', 'rejected'
            remark TEXT,
            reviewed_by_admin_id INTEGER,
            reviewed_at TEXT,
            FOREIGN KEY (company_id) REFERENCES company (id),
            FOREIGN KEY (reviewed_by_admin_id) REFERENCES users (id)
        );

            CREATE TABLE IF NOT EXISTS company_registration_requests (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_name TEXT NOT NULL,
              domain TEXT NOT NULL,
              website TEXT,
              industry TEXT,
              requested_agents TEXT,
              admin_full_name TEXT NOT NULL,
              admin_email TEXT NOT NULL,
              admin_password_hash TEXT NOT NULL,
              requested_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
              remark TEXT,
              reviewed_by_admin_id INTEGER,
              reviewed_at TEXT,
              FOREIGN KEY (reviewed_by_admin_id) REFERENCES users (id)
            );

            CREATE TABLE IF NOT EXISTS one_time_tokens (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              purpose TEXT NOT NULL,
              subject_type TEXT NOT NULL,
              subject_id INTEGER NOT NULL,
              token_hash TEXT NOT NULL UNIQUE,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_one_time_tokens_hash ON one_time_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_one_time_tokens_purpose_subject ON one_time_tokens(purpose, subject_type, subject_id);
            CREATE INDEX IF NOT EXISTS idx_one_time_tokens_exp ON one_time_tokens(expires_at);

            CREATE TABLE IF NOT EXISTS user_invitations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              email TEXT NOT NULL,
              full_name TEXT NOT NULL,
              role TEXT NOT NULL CHECK (role IN ('admin','member')),
              invited_by_user_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','cancelled')),
              invited_at TEXT NOT NULL,
              accepted_at TEXT,
              FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY(invited_by_user_id) REFERENCES users(id) ON DELETE CASCADE,
              UNIQUE(company_id, email, status)
            );
            CREATE INDEX IF NOT EXISTS idx_user_invitations_company_status ON user_invitations(company_id, status);
            CREATE INDEX IF NOT EXISTS idx_user_invitations_email ON user_invitations(email);

            CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);
            CREATE INDEX IF NOT EXISTS idx_login_requests_status ON login_requests(status);
        CREATE INDEX IF NOT EXISTS idx_login_requests_company ON login_requests(company_id);
            CREATE INDEX IF NOT EXISTS idx_company_reg_requests_status ON company_registration_requests(status);
            CREATE INDEX IF NOT EXISTS idx_company_reg_requests_domain ON company_registration_requests(domain);



            CREATE TABLE IF NOT EXISTS contact_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                subject TEXT,
                category TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_contact_messages_created_at ON contact_messages(created_at);

            CREATE TABLE IF NOT EXISTS agent_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_code TEXT NOT NULL,
                onedrive_link TEXT,
                website_link TEXT,
                access_token TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES company (id) ON DELETE CASCADE,
                UNIQUE(company_id, agent_code)
            );
            CREATE INDEX IF NOT EXISTS idx_agent_resources_company ON agent_resources(company_id);

            CREATE TABLE IF NOT EXISTS agent_creation_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER,
                agent_name TEXT NOT NULL,
                payload TEXT NOT NULL, -- JSON payload sent to remote
                status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','provisioning','deployed','failed','disabled')),
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS custom_agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                remote_id TEXT, -- The ID returned by the remote ingestion server
                configuration TEXT NOT NULL, -- JSON string with tools and knowledge base
                provisioning_status TEXT NOT NULL DEFAULT 'draft' CHECK (provisioning_status IN ('draft','submitted','provisioning','deployed','failed','disabled')),
                provisioning_error TEXT,
                last_provisioning_request_id INTEGER,
                provisioned_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(last_provisioning_request_id) REFERENCES agent_creation_requests(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_custom_agents_company ON custom_agents(company_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_agents_id_company ON custom_agents(id, company_id);

            CREATE TABLE IF NOT EXISTS companies (
                company_id INTEGER PRIMARY KEY,
                name TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agents (
                company_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                tools TEXT NOT NULL,
                workflow_path TEXT,
                status TEXT NOT NULL DEFAULT 'deployed',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                PRIMARY KEY (company_id, agent_name),
                FOREIGN KEY(company_id) REFERENCES companies(company_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_agents_company ON agents(company_id);

            CREATE TABLE IF NOT EXISTS kb_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                collection_id TEXT NOT NULL,
                web_url TEXT,
                onedrive_folder_id TEXT,
                onedrive_token TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(company_id, agent_name),
                FOREIGN KEY(company_id, agent_name) REFERENCES agents(company_id, agent_name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_kb_details_agent ON kb_details(company_id, agent_name);

            CREATE TABLE IF NOT EXISTS kb_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                source_type TEXT NOT NULL CHECK (source_type IN ('website','onedrive','sharepoint')),
                source_value TEXT NOT NULL,
                display_name TEXT,
                qdrant_collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','deleted')),
                last_ingested_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                UNIQUE(id, company_id, agent_id),
                UNIQUE(company_id, agent_id, source_type, source_value)
            );
            CREATE INDEX IF NOT EXISTS idx_kb_sources_company_agent ON kb_sources(company_id, agent_id);
            CREATE INDEX IF NOT EXISTS idx_kb_sources_status ON kb_sources(status);

            CREATE TABLE IF NOT EXISTS kb_ingestion_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                qdrant_collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
                documents_found INTEGER,
                chunks_indexed INTEGER,
                error_message TEXT,
                requested_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                FOREIGN KEY(source_id, company_id, agent_id) REFERENCES kb_sources(id, company_id, agent_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_company_agent ON kb_ingestion_jobs(company_id, agent_id);
            CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_source ON kb_ingestion_jobs(source_id);
            CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_status ON kb_ingestion_jobs(status);

            CREATE TABLE IF NOT EXISTS apis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                api_endpoints TEXT,
                req_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(company_id, agent_name),
                FOREIGN KEY(company_id, agent_name) REFERENCES agents(company_id, agent_name) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_apis_agent ON apis(company_id, agent_name);

            CREATE TABLE IF NOT EXISTS custom_agent_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                chat_session_id INTEGER,
                message_type TEXT NOT NULL CHECK (message_type IN ('user','bot','tool_result')),
                action_type TEXT,
                content TEXT NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_custom_agent_chat_messages_lookup
                ON custom_agent_chat_messages(company_id, agent_id, user_id, id);

            CREATE TABLE IF NOT EXISTS custom_agent_chat_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_custom_agent_chat_sessions_lookup
                ON custom_agent_chat_sessions(company_id, agent_id, user_id, updated_at);

            CREATE TABLE IF NOT EXISTS database_tool_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                user_id INTEGER,
                chat_session_id INTEGER,
                profile_index INTEGER NOT NULL,
                profile_name TEXT,
                engine TEXT,
                operation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending_user_confirmation',
                sql_text TEXT NOT NULL,
                sql_hash TEXT NOT NULL,
                request_message TEXT,
                estimated_affected_rows INTEGER,
                preview_error TEXT,
                affected_rows INTEGER,
                max_affected_rows INTEGER,
                result_json TEXT,
                error_message TEXT,
                approved_by_user_id INTEGER,
                approved_at TEXT,
                rejected_by_user_id INTEGER,
                rejected_at TEXT,
                rejection_reason TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(approved_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(rejected_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_database_tool_audit_company_status
                ON database_tool_audit_logs(company_id, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_database_tool_audit_agent
                ON database_tool_audit_logs(company_id, agent_id, created_at);

            CREATE TABLE IF NOT EXISTS tool_execution_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                chat_session_id INTEGER,
                tool_id TEXT NOT NULL,
                tool_kind TEXT NOT NULL CHECK (tool_kind IN ('api','mcp')),
                tool_name TEXT,
                side_effect TEXT NOT NULL,
                approval_policy TEXT NOT NULL,
                configuration_fingerprint TEXT NOT NULL,
                target_json TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                execution_arguments_json TEXT,
                request_message TEXT,
                status TEXT NOT NULL DEFAULT 'pending_user_confirmation'
                    CHECK (status IN ('pending_user_confirmation','executing','executed','cancelled','failed')),
                result_json TEXT,
                error_message TEXT,
                approved_by_user_id INTEGER,
                approved_at TEXT,
                rejected_by_user_id INTEGER,
                rejected_at TEXT,
                rejection_reason TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT,
                FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(approved_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(rejected_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tool_execution_audit_company_status
                ON tool_execution_audit_logs(company_id, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_tool_execution_audit_agent
                ON tool_execution_audit_logs(company_id, agent_id, created_at);

            CREATE TABLE IF NOT EXISTS agent_creation_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER,
                agent_name TEXT NOT NULL,
                payload TEXT NOT NULL, -- JSON payload sent to remote
                status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','provisioning','deployed','failed','disabled')),
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agent_creation_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                status_code INTEGER,
                response_body TEXT, -- JSON or raw response
                created_at TEXT NOT NULL,
                FOREIGN KEY (request_id) REFERENCES agent_creation_requests(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pending_payments (
                session_id TEXT PRIMARY KEY,
                company_id INTEGER NOT NULL,
                user_email TEXT NOT NULL,
                agent_payload TEXT NOT NULL,
                is_edit INTEGER NOT NULL DEFAULT 0,
                agent_id INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                expires_at TEXT,
                trial_used INTEGER NOT NULL DEFAULT 0 CHECK (trial_used IN (0,1)),
                trial_started_at TEXT,
                trial_expires_at TEXT,
                messages_used INTEGER NOT NULL DEFAULT 0,
                storage_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (workspace_id) REFERENCES company(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_subscriptions_workspace ON subscriptions(workspace_id);
            CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

            CREATE TABLE IF NOT EXISTS company_chat_usage (
                company_id INTEGER PRIMARY KEY,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                messages_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_company_chat_usage_period ON company_chat_usage(period_start, period_end);

            CREATE TABLE IF NOT EXISTS user_chat_quota_overrides (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                monthly_limit INTEGER,
                monthly_token_limit INTEGER,
                is_suspended INTEGER NOT NULL DEFAULT 0 CHECK (is_suspended IN (0,1)),
                suspended_until TEXT,
                reason TEXT,
                updated_by_user_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, user_id),
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS user_chat_usage (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                messages_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, user_id),
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_user_chat_usage_period ON user_chat_usage(period_start, period_end);

            CREATE TABLE IF NOT EXISTS chat_token_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_response_id TEXT NOT NULL UNIQUE,
                company_id INTEGER NOT NULL,
                user_id INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_token_usage_events_company_period
                ON chat_token_usage_events(company_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_chat_token_usage_events_user_period
                ON chat_token_usage_events(company_id, user_id, created_at);

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                transaction_id TEXT NOT NULL UNIQUE,
                payment_gateway TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                payment_status TEXT NOT NULL DEFAULT 'pending',
                payment_date TEXT NOT NULL,
                purchase_type TEXT NOT NULL DEFAULT 'workspace_subscription',
                feature_key TEXT,
                stripe_subscription_id TEXT,
                stripe_invoice_id TEXT,
                FOREIGN KEY (workspace_id) REFERENCES company(id) ON DELETE CASCADE
            );

            """
        )

        # Backward-compatible migration for existing databases.
        company_reg_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(company_registration_requests)").fetchall()
        }
        if "remark" not in company_reg_cols:
            conn.execute("ALTER TABLE company_registration_requests ADD COLUMN remark TEXT")
        if "requested_agents" not in company_reg_cols:
            conn.execute("ALTER TABLE company_registration_requests ADD COLUMN requested_agents TEXT")

        company_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(company)").fetchall()
        }
        if "allowed_agents" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN allowed_agents TEXT")
        if "subscription_tier" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        if "subscription_status" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN subscription_status TEXT DEFAULT 'inactive'")
        if "subscription_expires_at" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN subscription_expires_at TEXT")
        if "onboarding_completed" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN onboarding_completed INTEGER DEFAULT 0")
        if "onboarding_seen_at" not in company_cols:
            conn.execute("ALTER TABLE company ADD COLUMN onboarding_seen_at TEXT")

        tool_execution_audit_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tool_execution_audit_logs)").fetchall()
        }
        if "execution_arguments_json" not in tool_execution_audit_cols:
            conn.execute("ALTER TABLE tool_execution_audit_logs ADD COLUMN execution_arguments_json TEXT")
        legacy_tool_audit_rows = conn.execute(
            "SELECT id, arguments_json, execution_arguments_json FROM tool_execution_audit_logs"
        ).fetchall()
        for row in legacy_tool_audit_rows:
            try:
                audit_arguments = json.loads(row["arguments_json"] or "{}")
            except (TypeError, ValueError):
                continue
            execution_arguments = row["execution_arguments_json"] or row["arguments_json"]
            redacted_arguments = json.dumps(tool_data_redactor.redact(audit_arguments), default=str)[:20000]
            if row["arguments_json"] != redacted_arguments or not row["execution_arguments_json"]:
                conn.execute(
                    "UPDATE tool_execution_audit_logs "
                    "SET arguments_json = ?, execution_arguments_json = ? WHERE id = ?",
                    (redacted_arguments, execution_arguments, row["id"]),
                )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS subscription_packages_config (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              config_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by_user_id INTEGER,
              FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS feature_addon_configs (
              feature_key TEXT PRIMARY KEY,
              display_name TEXT NOT NULL,
              monthly_price_cents INTEGER NOT NULL DEFAULT 0
                CHECK (monthly_price_cents >= 0),
              currency TEXT NOT NULL DEFAULT 'usd',
              monthly_quota INTEGER NOT NULL DEFAULT 0
                CHECK (monthly_quota >= 0),
              is_available INTEGER NOT NULL DEFAULT 0 CHECK (is_available IN (0,1)),
              stripe_product_id TEXT,
              stripe_price_id TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by_user_id INTEGER,
              FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS company_feature_subscriptions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              feature_key TEXT NOT NULL,
              status TEXT NOT NULL CHECK (
                status IN (
                  'checkout_pending',
                  'active',
                  'cancel_at_period_end',
                  'past_due',
                  'canceled',
                  'expired'
                )
              ),
              billing_source TEXT NOT NULL DEFAULT 'stripe'
                CHECK (billing_source IN ('stripe','legacy')),
              stripe_customer_id TEXT,
              stripe_subscription_id TEXT UNIQUE,
              stripe_subscription_item_id TEXT,
              stripe_checkout_session_id TEXT UNIQUE,
              stripe_price_id TEXT,
              monthly_price_cents INTEGER NOT NULL CHECK (monthly_price_cents >= 0),
              currency TEXT NOT NULL DEFAULT 'usd',
              monthly_quota INTEGER NOT NULL CHECK (monthly_quota >= 0),
              current_period_start TEXT,
              current_period_end TEXT,
              cancel_at_period_end INTEGER NOT NULL DEFAULT 0
                CHECK (cancel_at_period_end IN (0,1)),
              canceled_at TEXT,
              stripe_event_created_at INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (company_id, feature_key),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (feature_key) REFERENCES feature_addon_configs(feature_key)
                ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_company_feature_subscriptions_status
              ON company_feature_subscriptions(feature_key, status);

            CREATE TABLE IF NOT EXISTS company_feature_usage_periods (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              feature_key TEXT NOT NULL,
              feature_subscription_id INTEGER NOT NULL,
              period_start TEXT NOT NULL,
              period_end TEXT NOT NULL,
              quota_limit INTEGER NOT NULL CHECK (quota_limit >= 0),
              units_used INTEGER NOT NULL DEFAULT 0 CHECK (units_used >= 0),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (company_id, feature_key, period_start),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (feature_key) REFERENCES feature_addon_configs(feature_key)
                ON DELETE RESTRICT,
              FOREIGN KEY (feature_subscription_id)
                REFERENCES company_feature_subscriptions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_company_feature_usage_period
              ON company_feature_usage_periods(company_id, feature_key, period_end);

            CREATE TABLE IF NOT EXISTS company_feature_usage_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              usage_period_id INTEGER NOT NULL,
              company_id INTEGER NOT NULL,
              feature_key TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              event_type TEXT NOT NULL,
              units INTEGER NOT NULL DEFAULT 1 CHECK (units > 0),
              created_at TEXT NOT NULL,
              UNIQUE (company_id, feature_key, idempotency_key),
              FOREIGN KEY (usage_period_id)
                REFERENCES company_feature_usage_periods(id) ON DELETE CASCADE,
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (feature_key) REFERENCES feature_addon_configs(feature_key)
                ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_company_feature_usage_events_period
              ON company_feature_usage_events(usage_period_id, created_at);

            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
              event_id TEXT PRIMARY KEY,
              event_type TEXT NOT NULL,
              livemode INTEGER NOT NULL DEFAULT 0 CHECK (livemode IN (0,1)),
              payload_json TEXT NOT NULL,
              processing_status TEXT NOT NULL DEFAULT 'received' CHECK (
                processing_status IN ('received','processing','completed','failed')
              ),
              attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              processed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_processing
              ON stripe_webhook_events(processing_status, created_at);

            INSERT OR IGNORE INTO feature_addon_configs (
              feature_key,
              display_name,
              monthly_price_cents,
              currency,
              monthly_quota,
              is_available,
              stripe_product_id,
              stripe_price_id,
              created_at,
              updated_at,
              updated_by_user_id
            )
            VALUES (
              'whatsapp',
              'WhatsApp Add-on',
              0,
              'usd',
              0,
              0,
              NULL,
              NULL,
              datetime('now'),
              datetime('now'),
              NULL
            );

            CREATE TABLE IF NOT EXISTS company_feature_entitlements (
              company_id INTEGER NOT NULL,
              feature_key TEXT NOT NULL,
              is_enabled INTEGER NOT NULL DEFAULT 0 CHECK (is_enabled IN (0,1)),
              enabled_at TEXT,
              disabled_at TEXT,
              updated_at TEXT NOT NULL,
              updated_by_user_id INTEGER,
              PRIMARY KEY (company_id, feature_key),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_company_feature_entitlements_enabled
              ON company_feature_entitlements(feature_key, is_enabled);

            CREATE TABLE IF NOT EXISTS company_whatsapp_connections (
              company_id INTEGER PRIMARY KEY,
              phone_number TEXT NOT NULL UNIQUE,
              provider TEXT NOT NULL DEFAULT 'meta'
                CHECK (provider IN ('meta','waha')),
              webhook_key TEXT UNIQUE,
              verification_token_secret TEXT,
              meta_app_secret TEXT,
              meta_phone_number_id TEXT,
              meta_access_token_secret TEXT,
              waha_session TEXT,
              waha_api_key_secret TEXT,
              webhook_verified_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by_user_id INTEGER,
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS whatsapp_inbound_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              meta_message_id TEXT NOT NULL,
              sender_phone TEXT,
              recipient_phone_number_id TEXT,
              message_type TEXT NOT NULL,
              message_text TEXT,
              payload_json TEXT NOT NULL,
              received_at TEXT NOT NULL,
              UNIQUE(company_id, meta_message_id),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_whatsapp_inbound_company_received
              ON whatsapp_inbound_messages(company_id, received_at);

            CREATE TABLE IF NOT EXISTS whatsapp_conversations (
              company_id INTEGER NOT NULL,
              sender_phone TEXT NOT NULL,
              selected_agent_id INTEGER,
              state TEXT NOT NULL DEFAULT 'awaiting_selection'
                CHECK (state IN ('awaiting_selection','active')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (company_id, sender_phone),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (selected_agent_id)
                REFERENCES custom_agents(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_agent
              ON whatsapp_conversations(company_id, selected_agent_id);

            CREATE TABLE IF NOT EXISTS whatsapp_message_dispatches (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              meta_message_id TEXT NOT NULL,
              sender_phone TEXT NOT NULL,
              action TEXT NOT NULL,
              selected_agent_id INTEGER,
              response_text TEXT,
              processing_status TEXT NOT NULL DEFAULT 'pending',
              attempt_count INTEGER NOT NULL DEFAULT 0,
              agent_response_text TEXT,
              last_error TEXT,
              processing_started_at TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              UNIQUE (company_id, meta_message_id),
              FOREIGN KEY (company_id, meta_message_id)
                REFERENCES whatsapp_inbound_messages(company_id, meta_message_id)
                ON DELETE CASCADE,
              FOREIGN KEY (selected_agent_id)
                REFERENCES custom_agents(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_whatsapp_dispatches_company_sender
              ON whatsapp_message_dispatches(company_id, sender_phone, id);
            CREATE INDEX IF NOT EXISTS idx_whatsapp_dispatches_processing
              ON whatsapp_message_dispatches(processing_status, id);

            CREATE TABLE IF NOT EXISTS whatsapp_outbound_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id INTEGER NOT NULL,
              dispatch_id INTEGER NOT NULL,
              recipient_phone TEXT NOT NULL,
              sequence_number INTEGER NOT NULL,
              message_text TEXT NOT NULL,
              send_status TEXT NOT NULL DEFAULT 'pending',
              meta_status TEXT,
              meta_message_id TEXT UNIQUE,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,
              created_at TEXT NOT NULL,
              sent_at TEXT,
              status_updated_at TEXT,
              UNIQUE (dispatch_id, sequence_number),
              FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
              FOREIGN KEY (dispatch_id)
                REFERENCES whatsapp_message_dispatches(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_whatsapp_outbound_dispatch
              ON whatsapp_outbound_messages(dispatch_id, sequence_number);
            CREATE INDEX IF NOT EXISTS idx_whatsapp_outbound_meta_message
              ON whatsapp_outbound_messages(meta_message_id);
            """
        )

        whatsapp_connection_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(company_whatsapp_connections)").fetchall()
        }
        whatsapp_connection_migrations = {
            "provider": "TEXT NOT NULL DEFAULT 'meta'",
            "webhook_key": "TEXT",
            "verification_token_secret": "TEXT",
            "meta_app_secret": "TEXT",
            "meta_phone_number_id": "TEXT",
            "meta_access_token_secret": "TEXT",
            "waha_session": "TEXT",
            "waha_api_key_secret": "TEXT",
            "webhook_verified_at": "TEXT",
        }
        for column_name, column_type in whatsapp_connection_migrations.items():
            if column_name not in whatsapp_connection_cols:
                conn.execute(
                    f"ALTER TABLE company_whatsapp_connections ADD COLUMN {column_name} {column_type}"
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_company_whatsapp_webhook_key "
            "ON company_whatsapp_connections(webhook_key)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_company_whatsapp_meta_phone_number_id "
            "ON company_whatsapp_connections(meta_phone_number_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_company_whatsapp_waha_session "
            "ON company_whatsapp_connections(waha_session)"
        )

        whatsapp_dispatch_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(whatsapp_message_dispatches)").fetchall()
        }
        whatsapp_dispatch_migrations = {
            "processing_status": "TEXT NOT NULL DEFAULT 'pending'",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "agent_response_text": "TEXT",
            "last_error": "TEXT",
            "processing_started_at": "TEXT",
            "completed_at": "TEXT",
        }
        for column_name, column_type in whatsapp_dispatch_migrations.items():
            if column_name not in whatsapp_dispatch_cols:
                conn.execute(
                    f"ALTER TABLE whatsapp_message_dispatches ADD COLUMN {column_name} {column_type}"
                )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_whatsapp_dispatches_processing "
            "ON whatsapp_message_dispatches(processing_status, id)"
        )

        payment_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(payments)").fetchall()
        }
        new_payment_cols = {
            "id",
            "workspace_id",
            "transaction_id",
            "payment_gateway",
            "amount",
            "currency",
            "payment_status",
            "payment_date",
        }
        if payment_cols and not new_payment_cols.issubset(payment_cols):
            print("DEBUG: Migrating payments table to workspace payment ledger...")
            conn.execute("ALTER TABLE payments RENAME TO payments_legacy")
            conn.executescript(
                """
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    transaction_id TEXT NOT NULL UNIQUE,
                    payment_gateway TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'usd',
                    payment_status TEXT NOT NULL DEFAULT 'pending',
                    payment_date TEXT NOT NULL,
                    stripe_session_id TEXT,
                    expires_at TEXT,
                    plan_name TEXT,
                    purchase_type TEXT NOT NULL DEFAULT 'workspace_subscription',
                    feature_key TEXT,
                    stripe_subscription_id TEXT,
                    stripe_invoice_id TEXT,
                    FOREIGN KEY (workspace_id) REFERENCES company(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_payments_workspace ON payments(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(payment_status);
                CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date);
                """
            )
            legacy_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(payments_legacy)").fetchall()
            }
            if {"company_id", "amount", "currency"}.issubset(legacy_cols):
                transaction_expr = "session_id" if "session_id" in legacy_cols else "order_id"
                if transaction_expr not in legacy_cols:
                    transaction_expr = "'legacy-payment-' || id"
                status_expr = "status" if "status" in legacy_cols else "'completed'"
                date_expr = "COALESCE(updated_at, created_at)" if {"updated_at", "created_at"}.issubset(legacy_cols) else "created_at"
                if "created_at" not in legacy_cols:
                    date_expr = "datetime('now')"
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO payments (
                        workspace_id,
                        transaction_id,
                        payment_gateway,
                        amount,
                        currency,
                        payment_status,
                        payment_date
                    )
                    SELECT
                        company_id,
                        COALESCE({transaction_expr}, 'legacy-payment-' || id),
                        'stripe',
                        amount,
                        currency,
                        COALESCE({status_expr}, 'completed'),
                        COALESCE({date_expr}, datetime('now'))
                    FROM payments_legacy
                    """
                )

        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_workspace ON payments(workspace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(payment_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_date ON payments(payment_date)")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS company_chat_usage (
                company_id INTEGER PRIMARY KEY,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                messages_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_company_chat_usage_period ON company_chat_usage(period_start, period_end);

            CREATE TABLE IF NOT EXISTS chat_token_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_response_id TEXT NOT NULL UNIQUE,
                company_id INTEGER NOT NULL,
                user_id INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_token_usage_events_company_period
                ON chat_token_usage_events(company_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_chat_token_usage_events_user_period
                ON chat_token_usage_events(company_id, user_id, created_at);
            """
        )

        company_chat_usage_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(company_chat_usage)").fetchall()
        }
        if "tokens_used" not in company_chat_usage_cols:
            conn.execute(
                "ALTER TABLE company_chat_usage ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0"
            )

        user_chat_usage_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(user_chat_usage)").fetchall()
        }
        if user_chat_usage_cols and "tokens_used" not in user_chat_usage_cols:
            conn.execute(
                "ALTER TABLE user_chat_usage ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0"
            )

        user_chat_quota_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(user_chat_quota_overrides)").fetchall()
        }
        if user_chat_quota_cols and "monthly_token_limit" not in user_chat_quota_cols:
            conn.execute(
                "ALTER TABLE user_chat_quota_overrides ADD COLUMN monthly_token_limit INTEGER"
            )
        if user_chat_quota_cols:
            # Preserve legacy per-user caps using the same 1,000-token conversion
            # applied to stored subscription-package message allowances.
            conn.execute(
                """
                UPDATE user_chat_quota_overrides
                SET monthly_token_limit = monthly_limit * 1000
                WHERE monthly_token_limit IS NULL AND monthly_limit IS NOT NULL
                """
            )

        # Ensure stripe_session_id column exists
        payment_cols_info = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(payments)").fetchall()
        }
        if payment_cols_info and "stripe_session_id" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN stripe_session_id TEXT")
        if payment_cols_info and "expires_at" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN expires_at TEXT")
        if payment_cols_info and "plan_name" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN plan_name TEXT")
        if payment_cols_info and "purchase_type" not in payment_cols_info:
            conn.execute(
                "ALTER TABLE payments ADD COLUMN purchase_type TEXT NOT NULL "
                "DEFAULT 'workspace_subscription'"
            )
        if payment_cols_info and "feature_key" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN feature_key TEXT")
        if payment_cols_info and "stripe_subscription_id" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN stripe_subscription_id TEXT")
        if payment_cols_info and "stripe_invoice_id" not in payment_cols_info:
            conn.execute("ALTER TABLE payments ADD COLUMN stripe_invoice_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_stripe_invoice "
            "ON payments(stripe_invoice_id)"
        )

        feature_subscription_cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(company_feature_subscriptions)"
            ).fetchall()
        }
        if (
            feature_subscription_cols
            and "stripe_event_created_at" not in feature_subscription_cols
        ):
            conn.execute(
                "ALTER TABLE company_feature_subscriptions ADD COLUMN "
                "stripe_event_created_at INTEGER NOT NULL DEFAULT 0"
            )
        if feature_subscription_cols and "billing_source" not in feature_subscription_cols:
            conn.execute(
                "ALTER TABLE company_feature_subscriptions ADD COLUMN "
                "billing_source TEXT NOT NULL DEFAULT 'stripe'"
            )

        migrate_legacy_whatsapp_entitlements(conn)

        subscriptions_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()
        }
        if not subscriptions_cols:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    expires_at TEXT,
                    trial_used INTEGER NOT NULL DEFAULT 0 CHECK (trial_used IN (0,1)),
                    trial_started_at TEXT,
                    trial_expires_at TEXT,
                    messages_used INTEGER NOT NULL DEFAULT 0,
                    storage_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY (workspace_id) REFERENCES company(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_subscriptions_workspace ON subscriptions(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
                """
            )
        conn.execute(
            """
            INSERT INTO subscriptions (
                workspace_id,
                plan,
                status,
                started_at,
                expires_at,
                trial_used,
                messages_used,
                storage_used,
                created_at,
                updated_at
            )
            SELECT
                c.id,
                COALESCE(NULLIF(c.subscription_tier, ''), 'free'),
                COALESCE(NULLIF(c.subscription_status, ''), 'inactive'),
                c.created_at,
                c.subscription_expires_at,
                0,
                0,
                0,
                COALESCE(c.created_at, ?),
                ?
            FROM company c
            WHERE NOT EXISTS (
                SELECT 1 FROM subscriptions s WHERE s.workspace_id = c.id
            )
            """,
            (utcnow_iso(), utcnow_iso()),
        )

        login_request_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(login_requests)").fetchall()
        }
        if "remark" not in login_request_cols:
            conn.execute("ALTER TABLE login_requests ADD COLUMN remark TEXT")

        user_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "must_reset_password" not in user_cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN must_reset_password INTEGER NOT NULL DEFAULT 0 CHECK (must_reset_password IN (0,1))"
            )



        agent_res_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(agent_resources)").fetchall()
        }
        if "access_token" not in agent_res_cols:
            print("DEBUG: Migrating agent_resources table (access_token)...")
            conn.execute("ALTER TABLE agent_resources ADD COLUMN access_token TEXT")

        kb_sources_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'kb_sources'"
        ).fetchone()
        if kb_sources_sql and "sharepoint" not in (kb_sources_sql["sql"] or ""):
            print("DEBUG: Migrating kb_sources table (sharepoint source type)...")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP INDEX IF EXISTS idx_kb_sources_company_agent")
            conn.execute("DROP INDEX IF EXISTS idx_kb_sources_status")
            conn.execute("DROP INDEX IF EXISTS idx_kb_ingestion_jobs_company_agent")
            conn.execute("DROP INDEX IF EXISTS idx_kb_ingestion_jobs_source")
            conn.execute("DROP INDEX IF EXISTS idx_kb_ingestion_jobs_status")
            conn.execute("ALTER TABLE kb_sources RENAME TO kb_sources_old")
            conn.execute(
                """
                CREATE TABLE kb_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    agent_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL CHECK (source_type IN ('website','onedrive','sharepoint')),
                    source_value TEXT NOT NULL,
                    display_name TEXT,
                    qdrant_collection TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','deleted')),
                    last_ingested_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                    UNIQUE(id, company_id, agent_id),
                    UNIQUE(company_id, agent_id, source_type, source_value)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO kb_sources
                    (id, company_id, agent_id, source_type, source_value, display_name, qdrant_collection, status, last_ingested_at, created_at, updated_at)
                SELECT
                    id, company_id, agent_id, source_type, source_value, display_name, qdrant_collection, status, last_ingested_at, created_at, updated_at
                FROM kb_sources_old
                """
            )
            conn.execute("ALTER TABLE kb_ingestion_jobs RENAME TO kb_ingestion_jobs_old")
            conn.execute(
                """
                CREATE TABLE kb_ingestion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    agent_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    qdrant_collection TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','completed','failed','cancelled')),
                    documents_found INTEGER,
                    chunks_indexed INTEGER,
                    error_message TEXT,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id, company_id, agent_id) REFERENCES kb_sources(id, company_id, agent_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO kb_ingestion_jobs
                    (id, company_id, agent_id, source_id, qdrant_collection, status, documents_found, chunks_indexed, error_message, requested_at, started_at, finished_at)
                SELECT
                    id, company_id, agent_id, source_id, qdrant_collection, status, documents_found, chunks_indexed, error_message, requested_at, started_at, finished_at
                FROM kb_ingestion_jobs_old
                """
            )
            conn.execute("DROP TABLE kb_ingestion_jobs_old")
            conn.execute("DROP TABLE kb_sources_old")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_sources_company_agent ON kb_sources(company_id, agent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_sources_status ON kb_sources(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_company_agent ON kb_ingestion_jobs(company_id, agent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_source ON kb_ingestion_jobs(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kb_ingestion_jobs_status ON kb_ingestion_jobs(status)")
            conn.execute("PRAGMA foreign_keys = ON")

        custom_agent_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(custom_agents)").fetchall()
        }
        if "remote_id" not in custom_agent_cols:
            print("DEBUG: Migrating custom_agents table (remote_id)...")
            conn.execute("ALTER TABLE custom_agents ADD COLUMN remote_id TEXT")
        if "provisioning_status" not in custom_agent_cols:
            conn.execute("ALTER TABLE custom_agents ADD COLUMN provisioning_status TEXT NOT NULL DEFAULT 'deployed'")
        if "provisioning_error" not in custom_agent_cols:
            conn.execute("ALTER TABLE custom_agents ADD COLUMN provisioning_error TEXT")
        if "last_provisioning_request_id" not in custom_agent_cols:
            conn.execute("ALTER TABLE custom_agents ADD COLUMN last_provisioning_request_id INTEGER")
        if "provisioned_at" not in custom_agent_cols:
            conn.execute("ALTER TABLE custom_agents ADD COLUMN provisioned_at TEXT")
        if "description" not in custom_agent_cols:
            print("DEBUG: Migrating custom_agents table (description)...")
            conn.execute("ALTER TABLE custom_agents ADD COLUMN description TEXT")

        kb_sources_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'kb_sources'"
        ).fetchone()
        kb_sources_sql = kb_sources_row["sql"] if kb_sources_row else ""
        if kb_sources_sql and "sharepoint" not in kb_sources_sql.lower():
            print("DEBUG: Migrating kb_sources table (sharepoint source type)...")
            conn.commit()
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kb_sources_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    agent_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL CHECK (source_type IN ('website','onedrive','sharepoint')),
                    source_value TEXT NOT NULL,
                    display_name TEXT,
                    qdrant_collection TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','deleted')),
                    last_ingested_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT,
                    FOREIGN KEY(company_id) REFERENCES company(id) ON DELETE CASCADE,
                    FOREIGN KEY(agent_id, company_id) REFERENCES custom_agents(id, company_id) ON DELETE CASCADE,
                    UNIQUE(id, company_id, agent_id),
                    UNIQUE(company_id, agent_id, source_type, source_value)
                );
                INSERT OR IGNORE INTO kb_sources_new (
                    id,
                    company_id,
                    agent_id,
                    source_type,
                    source_value,
                    display_name,
                    qdrant_collection,
                    status,
                    last_ingested_at,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    company_id,
                    agent_id,
                    source_type,
                    source_value,
                    display_name,
                    qdrant_collection,
                    status,
                    last_ingested_at,
                    created_at,
                    updated_at
                FROM kb_sources;
                DROP TABLE kb_sources;
                ALTER TABLE kb_sources_new RENAME TO kb_sources;
                CREATE INDEX IF NOT EXISTS idx_kb_sources_company_agent ON kb_sources(company_id, agent_id);
                CREATE INDEX IF NOT EXISTS idx_kb_sources_status ON kb_sources(status);
                """
            )
            conn.commit()
            conn.execute("PRAGMA foreign_keys = ON;")

        chat_message_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(custom_agent_chat_messages)").fetchall()
        }
        if "chat_session_id" not in chat_message_cols:
            conn.execute("ALTER TABLE custom_agent_chat_messages ADD COLUMN chat_session_id INTEGER")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_custom_agent_chat_messages_session
                ON custom_agent_chat_messages(company_id, agent_id, user_id, chat_session_id, id)
            """
        )

        legacy_threads = conn.execute(
            """
            SELECT company_id, agent_id, user_id, MIN(created_at) AS created_at, MAX(created_at) AS updated_at
            FROM custom_agent_chat_messages
            WHERE chat_session_id IS NULL
            GROUP BY company_id, agent_id, user_id
            """
        ).fetchall()
        for thread in legacy_threads:
            cur = conn.execute(
                """
                INSERT INTO custom_agent_chat_sessions
                    (company_id, agent_id, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread["company_id"],
                    thread["agent_id"],
                    thread["user_id"],
                    "Previous chat",
                    thread["created_at"] or utcnow_iso(),
                    thread["updated_at"] or utcnow_iso(),
                ),
            )
            conn.execute(
                """
                UPDATE custom_agent_chat_messages
                SET chat_session_id = ?
                WHERE company_id = ? AND agent_id = ? AND user_id = ? AND chat_session_id IS NULL
                """,
                (
                    int(cur.lastrowid),
                    thread["company_id"],
                    thread["agent_id"],
                    thread["user_id"],
                ),
            )

        agent_creation_request_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(agent_creation_requests)").fetchall()
        }
        if "agent_id" not in agent_creation_request_cols:
            conn.execute("ALTER TABLE agent_creation_requests ADD COLUMN agent_id INTEGER")
        if "status" not in agent_creation_request_cols:
            conn.execute("ALTER TABLE agent_creation_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'deployed'")
        if "error_message" not in agent_creation_request_cols:
            conn.execute("ALTER TABLE agent_creation_requests ADD COLUMN error_message TEXT")
        if "completed_at" not in agent_creation_request_cols:
            conn.execute("ALTER TABLE agent_creation_requests ADD COLUMN completed_at TEXT")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_custom_agents_provisioning_status ON custom_agents(provisioning_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_creation_requests_company_agent ON agent_creation_requests(company_id, agent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_creation_requests_status ON agent_creation_requests(status)")

        print("DEBUG: Database initialization complete.")

    from subscription_packages import seed_packages_config

    with get_conn() as conn:
        seed_packages_config(conn)


def fetch_one(conn, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    cur.close()
    return row


def fetch_all(conn, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows



