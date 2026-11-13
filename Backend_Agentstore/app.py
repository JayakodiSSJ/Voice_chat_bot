from flask import Flask, render_template, jsonify, request
import requests
from flask_cors import CORS
import os
from dotenv import load_dotenv
import stripe
import traceback
from werkzeug.exceptions import HTTPException


load_dotenv()
from auth_routes import register_auth_routes
from flask_mail import Mail, Message
from auth_sqlite import get_conn, is_valid_email, normalize_email, utcnow_iso
from email_branding import build_email_html, build_email_text, get_sender_identity, attach_email_logo

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
# Enable CORS for API routes (needed for localhost dev + browser preflight OPTIONS)


# Mail config
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")
app.config["FRONTEND_URL"] = os.getenv("FRONTEND_URL", "http://localhost:3000")
app.config["PUBLIC_BACKEND_URL"] = os.getenv("PUBLIC_BACKEND_URL", "")
app.config["WHATSAPP_GRAPH_API_VERSION"] = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v26.0")
app.config["WHATSAPP_API_TIMEOUT_SECONDS"] = float(os.getenv("WHATSAPP_API_TIMEOUT_SECONDS", "20"))
app.config["WHATSAPP_WORKER_THREADS"] = int(os.getenv("WHATSAPP_WORKER_THREADS", "2"))
app.config["WAHA_BASE_URL"] = os.getenv("WAHA_BASE_URL", "")
app.config["SUPER_ADMIN_DOMAIN"] = os.getenv("SUPER_ADMIN_DOMAIN", "intranet.slt.com.lk")
app.config["AUTH_SECRET_KEY"] = os.getenv("AUTH_SECRET_KEY")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or os.getenv("AUTH_SECRET_KEY")

mail = Mail(app)


def _cors_allowed_origins() -> list[str]:
    configured = [
        app.config["FRONTEND_URL"],
        "https://aisleagents.raccoon-ai.com",
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5001",
        "http://127.0.0.1:5001",
    ]
    configured.extend(
        origin.strip()
        for origin in (os.getenv("CORS_ALLOWED_ORIGINS") or "").split(",")
        if origin.strip()
    )

    origins = []
    for origin in configured:
        normalized = str(origin or "").strip().rstrip("/")
        if normalized and normalized not in origins:
            origins.append(normalized)
    return origins


CORS(
    app,
    resources={
        r"/api/*": {
            "origins": _cors_allowed_origins(),
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
            "expose_headers": ["Content-Type"],
            "max_age": 600,
        }
    },
)
register_auth_routes(app, mail)


@app.errorhandler(Exception)
def handle_api_exception(error):
    if not request.path.startswith("/api/"):
        if isinstance(error, HTTPException):
            return error
        raise error

    if isinstance(error, HTTPException):
        return jsonify(
            {
                "error": error.name.lower().replace(" ", "_"),
                "message": error.description,
            }
        ), error.code or 500

    traceback.print_exc()
    return jsonify(
        {
            "error": "internal_server_error",
            "message": str(error) or "Internal server error",
        }
    ), 500


def _frontend_url() -> str:
    raw = (app.config.get("FRONTEND_URL") or "https://aisleagents.raccoon-ai.com").strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def _build_message(subject: str, recipients: list[str], reply_to: str | None = None) -> Message:
    kwargs = {"subject": subject, "recipients": recipients}
    sender = get_sender_identity(app)
    if sender:
        kwargs["sender"] = sender
    if reply_to:
        kwargs["reply_to"] = reply_to
    return Message(**kwargs)


def _send_message_with_fallback(msg: Message, *, label: str) -> tuple[bool, str | None]:
    try:
        attach_email_logo(app, msg)
        mail.send(msg)
        return True, None
    except Exception as first_error:
        first_text = str(first_error)
        print(f"ERROR: Failed to send {label}: {first_text}")

        # Retry with the plain sender email only (without display-name tuple),
        # which helps with stricter SMTP providers.
        fallback_sender = _mail_default_sender_email()
        retry_kwargs = {
            "subject": msg.subject,
            "recipients": list(msg.recipients or []),
        }
        if fallback_sender:
            retry_kwargs["sender"] = fallback_sender
        if msg.reply_to:
            retry_kwargs["reply_to"] = msg.reply_to

        try:
            retry_msg = Message(**retry_kwargs)
            retry_msg.body = msg.body
            retry_msg.html = msg.html
            attach_email_logo(app, retry_msg)
            mail.send(retry_msg)
            print(f"WARN: {label} send succeeded on fallback sender retry.")
            return True, None
        except Exception as retry_error:
            retry_text = str(retry_error)
            print(f"ERROR: Fallback retry failed for {label}: {retry_text}")
            return False, f"{first_text}; retry_failed: {retry_text}"


def _mail_default_sender_email() -> str | None:
    sender = app.config.get("MAIL_DEFAULT_SENDER")

    if isinstance(sender, (tuple, list)) and len(sender) >= 2:
        normalized = normalize_email(str(sender[1]))
        return normalized or None

    if isinstance(sender, str):
        text = sender.strip()
        if "<" in text and ">" in text:
            text = text.split("<", 1)[1].split(">", 1)[0].strip()
        normalized = normalize_email(text)
        return normalized or None

    return None


def _resolve_super_admin_recipients() -> list[str]:
    recipients: list[str] = []
    seen: set[str] = set()
    super_admin_domain = normalize_email(app.config.get("SUPER_ADMIN_DOMAIN") or "intranet.slt.com.lk")

    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT u.email
                FROM users u
                JOIN company c ON c.id = u.company_id
                WHERE u.role = 'admin' AND u.is_active = 1 AND lower(c.domain) = ?
                """,
                (super_admin_domain,),
            ).fetchall()

        for row in rows:
            email = normalize_email(row["email"])
            if email and email not in seen:
                seen.add(email)
                recipients.append(email)
    except Exception as e:
        print(f"WARN: Failed to resolve super admin recipients from DB: {e}")

    fallback = _mail_default_sender_email()
    if fallback and fallback not in seen:
        recipients.append(fallback)

    return recipients

PLACEMENT_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_internshipPlacementAgent/directline/token?api-version=2022-03-01-preview"
TEST_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_testAisleWeb/directline/token?api-version=2022-03-01-preview"
INNOSPACE_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_innoSpaceAssistant/directline/token?api-version=2022-03-01-preview"
EMBRYO_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_embryoAgent/directline/token?api-version=2022-03-01-preview"
DIGITAL_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_agent1/directline/token?api-version=2022-03-01-preview"
SLTMNIT_LINE_TOKEN_URL = "https://default534253fcdfb6462fb5cacbe81939f5.ee.environment.api.powerplatform.com/powervirtualagents/botsbyschema/crad5_agent1_HZo0vV/directline/token?api-version=2022-03-01-preview"


EKB_API_URL = "https://querio.raccoon-ai.io/api/v1/chat/vector_multi"
EKB_API_KEY = "ekb_P_HHBtG8vRYywCmQUe-tzQ"



@app.route('/')
def index():
    return render_template('index.html')

# Diagnostic route
@app.route('/api/ping')
def ping():
    return jsonify({"status": "ok", "message": "Backend is responsive"}), 200


#email
@app.post("/api/send-email")
def send_email():
    data = request.get_json() or {}
    
    # Handle "Contact Us" form submissions
    if "name" in data and "message" in data:
        name = (data.get("name") or "").strip()
        raw_sender_email = (data.get("email") or "").strip()
        sender_email = normalize_email(raw_sender_email)
        sender_email_for_reply = raw_sender_email if is_valid_email(raw_sender_email) else sender_email
        category = data.get("category", "General Inquiry")
        subject = data.get("subject", f"New Contact Us Inquiry: {category}")
        message = (data.get("message") or "").strip()
        created_at = utcnow_iso()
        inquiry_id = None

        if not is_valid_email(sender_email_for_reply):
            return jsonify(
                {
                    "error": "A valid email address is required to send inquiry confirmation.",
                    "acknowledgment_sent": False,
                }
            ), 400
        
        try:
            with get_conn() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO contact_messages (name, email, subject, category, message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (name, sender_email, subject, category, message, created_at)
                )
                inquiry_id = int(cur.lastrowid)
        except Exception as e:
            print(f"Error saving contact message to DB: {e}")

        recipients = _resolve_super_admin_recipients()
        if not recipients:
            return jsonify({"error": "Super Admin email not configured"}), 500
            
        try:
            details = [
                ("Inquiry ID", str(inquiry_id)) if inquiry_id else None,
                ("Name", name),
                ("Email", sender_email_for_reply or sender_email),
                ("Category", category),
                ("Subject", subject),
                ("Received (UTC)", created_at),
                ("Message", message),
            ]
            details = [item for item in details if item]
            reply_to = sender_email_for_reply if is_valid_email(sender_email_for_reply) else None
            msg = _build_message(subject=subject, recipients=recipients, reply_to=reply_to)
            msg.body = build_email_text(
                subject=subject,
                intro_text="You have received a new inquiry from the Contact Us page.",
                details=details,
                cta_text="Open Agent Store",
                cta_url=_frontend_url(),
            )
            msg.html = build_email_html(
                app,
                subject=subject,
                intro_text="You have received a new inquiry from the Contact Us page.",
                details=details,
                cta_text="Open Agent Store",
                cta_url=_frontend_url(),
            )
            sent_to_admins, admin_send_error = _send_message_with_fallback(
                msg,
                label="contact notification to super admins",
            )
            if not sent_to_admins:
                return jsonify({"error": admin_send_error or "Failed to send inquiry email"}), 500

            # Send an acknowledgment email to the user with the same branded styling.
            ack_sent = False
            ack_error = None
            if is_valid_email(sender_email_for_reply):
                ack_subject = "Inquiry Received - Agent Store"
                greeting_name = name or "there"
                ack_intro = (
                    f"Hi {greeting_name},\n\n"
                    "Thank you for reaching out to Agent Store. Your inquiry has been submitted successfully and is now in our support queue.\n\n"
                    "Our team is reviewing your request and will contact you shortly, usually within one business day.\n\n"
                    "If you need to share more details, you can reply to this email and mention your Inquiry ID."
                )
                ack_details = [
                    ("Inquiry ID", str(inquiry_id)) if inquiry_id else None,
                    ("Submitted By", name or "N/A"),
                    ("Email", sender_email_for_reply),
                    ("Category", category),
                    ("Subject", subject),
                    ("Submitted At (UTC)", created_at),
                ]
                ack_details = [item for item in ack_details if item]

                ack_msg = _build_message(subject=ack_subject, recipients=[sender_email_for_reply])
                ack_msg.body = build_email_text(
                    subject=ack_subject,
                    intro_text=ack_intro,
                    details=ack_details,
                    cta_text="Open Agent Store",
                    cta_url=_frontend_url(),
                )
                ack_msg.html = build_email_html(
                    app,
                    subject=ack_subject,
                    intro_text=ack_intro,
                    details=ack_details,
                    cta_text="Open Agent Store",
                    cta_url=_frontend_url(),
                )

                ack_sent, ack_error = _send_message_with_fallback(
                    ack_msg,
                    label="contact acknowledgment to user",
                )
            else:
                ack_error = "Invalid sender email for acknowledgment delivery."

            response_payload = {
                "message": "Contact inquiry sent successfully",
                "acknowledgment_sent": bool(ack_sent),
            }
            if ack_error:
                response_payload["acknowledgment_error"] = ack_error

            return jsonify(response_payload), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Fallback to general email sending behavior for system notifications
    to_email = data.get("to")
    subject = data.get("subject", "No subject")
    body = data.get("body", "")

    if not to_email:
        return jsonify({"error": "Missing 'to' email"}), 400

    try:
        msg = _build_message(subject=subject, recipients=[to_email])
        msg.body = build_email_text(
            subject=subject,
            intro_text=body or "You have a new notification from Agent Store.",
            cta_text="Open Agent Store",
            cta_url=_frontend_url(),
        )
        msg.html = build_email_html(
            app,
            subject=subject,
            intro_text=body or "You have a new notification from Agent Store.",
            cta_text="Open Agent Store",
            cta_url=_frontend_url(),
        )
        attach_email_logo(app, msg)
        mail.send(msg)
        return jsonify({"message": "Email sent"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/get_token_placement')
def get_token_placement():
    try:
        response = requests.get(PLACEMENT_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_token_test')
def get_token_test():
    try:
        response = requests.get(TEST_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_token_innospace')
def get_token_innospace():
    try:
        response = requests.get(INNOSPACE_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_token_embryo')
def get_token_embryo():
    try:
        response = requests.get(EMBRYO_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_token_digital')
def get_token_digital():
    try:
        response = requests.get(DIGITAL_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_token_sltmnit')
def get_token_sltmnit():
    try:
        response = requests.get(SLTMNIT_LINE_TOKEN_URL)
        data = response.json()
        return jsonify({"token": data.get("token")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/chat_ekb', methods=['POST'])
def chat_ekb():
    try:
        headers = {
            'X-Api-Key': EKB_API_KEY,
            'Content-Type': 'application/json'
        }
        # Forward the request body to the EKB API
        response = requests.post(EKB_API_URL, 
                               json=request.json, 
                               headers=headers)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

################################################### DATABASE ROUTES ###################################################

@app.route('/api/user-role', methods=['GET'])
def get_user_role_api():
    try:
        email = request.headers.get('X-User-Email')
        if not email:
            return jsonify({'error': 'No email provided'}), 400
        
        role = get_user_role(email)
        if role is None:
            # User not found in database, add them with default 'user' role
            add_user(email, 'user')
            return jsonify({'role': 'user'}), 200
        
        return jsonify({'role': role}), 200
    
    except Exception as e:
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/api/admin/users', methods=['GET'])
def get_all_users_api():
    try:
        users = get_all_users()
        return jsonify({'users': users})  # Wrap in 'users' key to match frontend expectation
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Add this new POST endpoint
@app.route('/api/admin/users', methods=['POST'])
def add_user_api():
    try:
        data = request.json
        email = data.get('email')
        first_name = data.get('firstName')
        last_name = data.get('lastName')
        role = data.get('role')
        
        if not email or not first_name or not last_name or not role:
            return jsonify({'error': 'All fields are required'}), 400
        
        add_user(email, role, first_name, last_name)
        return jsonify({'message': 'User added successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/users/<email>', methods=['PUT'])
def update_user_api(email):
    try:
        from database import update_user
        data = request.json
        first_name = data.get('firstName')
        last_name = data.get('lastName')
        role = data.get('role')
        
        if not first_name or not last_name or not role:
            return jsonify({'error': 'First name, last name, and role are required'}), 400
        
        update_user(email, first_name, last_name, role)
        return jsonify({'message': 'User updated successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/users/<email>', methods=['DELETE'])
def delete_user_api(email):
    try:
        from database import delete_user
        delete_user(email)
        return jsonify({'message': 'User deleted successfully'})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True, use_reloader=False)
