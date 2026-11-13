<p align="center">
  <img src="documentation/assets/aisle-ai-agents-hero.png" alt="Aisle AI Agents — workspace automation platform" width="100%">
</p>

<h1 align="center">Aisle AI Agent Store</h1>

<p align="center">
  A multi-tenant platform for creating, configuring, deploying, and using secure AI agents.
</p>

Aisle AI Agent Store combines a React workspace with a Flask API, PostgreSQL, and Qdrant. Organizations can manage users and subscriptions, ground agents with company knowledge, connect tools, and make agents available through web chat or WhatsApp.

## Features

- Multi-tenant company workspaces with role-based administration
- Custom agent creation, configuration, provisioning, and chat history
- Knowledge ingestion from websites, OneDrive, SharePoint, and uploaded resources
- Retrieval-augmented responses backed by OpenAI embeddings and Qdrant
- API, MCP, database, and form tools with policy checks and audit logs
- Usage tracking, per-user quotas, subscription packages, and Stripe billing
- WhatsApp delivery through Meta Cloud API or WAHA
- Microsoft sign-in, email workflows, and company onboarding

## Architecture

| Component | Technology | Responsibility |
| --- | --- | --- |
| Frontend | React 19, Material UI, React Three Fiber | Landing pages, authentication, dashboards, agent configuration, and chat |
| Backend | Python 3.11, Flask, Gunicorn | REST API, authentication, orchestration, billing, ingestion, and integrations |
| Relational data | PostgreSQL 16 | Companies, users, agents, subscriptions, usage, conversations, and audit records |
| Vector data | Qdrant | Company-scoped knowledge-base embeddings and semantic retrieval |
| Edge | Nginx | Static frontend hosting, SPA routing, API proxying, and security headers |

The default Docker Compose stack exposes only the frontend on `127.0.0.1:8080`. Nginx forwards `/api` requests to the private backend service; PostgreSQL and Qdrant remain internal to the Compose network.

## Repository Layout

```text
.
├── Backend_Agentstore/    # Flask API, services, database initialization, and tests
├── chatbot/               # React frontend and production Nginx configuration
├── documentation/         # Deployment, authentication, and integration runbooks
├── kubernetes/            # Kubernetes manifests and setup guides
├── nginx/                 # Standalone production Nginx configuration
├── docker-compose.yml     # Production-style local stack
└── docker-compose.dev.yml # Local-only service port overrides
```

## Quick Start with Docker

### Prerequisites

- Docker Engine or Docker Desktop
- Docker Compose v2
- An OpenAI API key for agent chat and knowledge ingestion

### 1. Configure the backend

Create `Backend_Agentstore/.env`. A minimal local configuration is:

```dotenv
AUTH_SECRET_KEY=replace-with-a-long-random-secret
SECRET_KEY=replace-with-another-long-random-secret
OPENAI_API_KEY=replace-with-your-openai-api-key
FRONTEND_URL=http://localhost:8080
```

`docker-compose.yml` supplies `DATABASE_URL`, `QDRANT_URL`, and `QDRANT_VECTOR_SIZE` to the backend. Add integration-specific variables from the [deployment guide](documentation/deployment.md) only for the features you enable.

Do not commit `.env` or any production credentials.

### 2. Build and start the stack

```bash
docker compose up --build -d
```

Open <http://localhost:8080>. Confirm the API is reachable through the frontend proxy:

```bash
curl http://localhost:8080/api/ping
```

Expected response:

```json
{"message":"Backend is responsive","status":"ok"}
```

Useful lifecycle commands:

```bash
docker compose logs -f backend
docker compose ps
docker compose down
```

Named volumes retain PostgreSQL and Qdrant data when the containers stop. Use `docker compose down -v` only when you intentionally want to delete local data.

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+ and npm
- Docker Compose for PostgreSQL and Qdrant

### 1. Start the data services

The development override publishes services on loopback-only ports:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres qdrant
```

Add these values to `Backend_Agentstore/.env` along with the secrets shown above:

```dotenv
DATABASE_URL=postgresql://agentstore:agentstore_dev_password@localhost:5433/agentstore
QDRANT_URL=http://localhost:6335
QDRANT_VECTOR_SIZE=1536
FRONTEND_URL=http://localhost:3000
```

### 2. Run the backend

```bash
cd Backend_Agentstore
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS or Linux
source .venv/bin/activate
```

Install dependencies and start Flask:

```bash
python -m pip install -r requirements.txt
python app.py
```

The development server listens on <http://localhost:5002>. Database tables are initialized automatically when the backend starts.

### 3. Run the frontend

In a second terminal:

```bash
cd chatbot
npm ci --legacy-peer-deps
npm start
```

The React development server opens <http://localhost:3000> and proxies API requests to `http://127.0.0.1:5002`.

## Configuration

Common backend environment variables include:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `QDRANT_URL` | Qdrant service URL | `http://localhost:6333` |
| `QDRANT_API_KEY` | Qdrant authentication, when enabled | None |
| `OPENAI_API_KEY` | Chat and embedding requests | Required for AI features |
| `OPENAI_SMALL_MODEL` | Lightweight task model | `gpt-4o-mini` |
| `OPENAI_LARGE_MODEL` | Tool-planning task model | `gpt-4o-mini` |
| `OPENAI_EMBEDDING_MODEL` | Knowledge-base embedding model | `text-embedding-3-small` |
| `GEMINI_API_KEY` | Server-side Gemini Developer API key for STT and TTS | Required for voice features |
| `GEMINI_TRANSCRIBE_MODEL` | Gemini speech transcription model | `gemini-3.5-transcribe` |
| `GEMINI_TTS_MODEL` | Gemini TTS model for English and code-mixed responses | `gemini-3.8-flash-lite-tts` |
| `GEMINI_TTS_SINHALA_VOICE` | Optional Gemini prebuilt voice for unsupported Sinhala code-mixing | `Aoede` |
| `GEMINI_TTS_ENGLISH_VOICE` | Optional Gemini prebuilt voice for English responses | `Aoede` |
| `GEMINI_TTS_VOICE` | Legacy shared Gemini voice override | `Aoede` |
| `PIPER_SINHALA_MODEL_PATH` | Local Piper Sinhala ONNX voice path | `/app/models/si_LK-sinhala-medium.onnx` |
| `GEMINI_UPLOAD_TIMEOUT_SECONDS` | Gemini audio upload timeout | `30` |
| `GEMINI_STT_TIMEOUT_SECONDS` | Gemini speech recognition timeout | `90` |
| `GEMINI_TTS_TIMEOUT_SECONDS` | Gemini speech synthesis timeout | `90` |
| `AUTH_SECRET_KEY` | Token signing and sensitive-value encryption | Required |
| `FRONTEND_URL` | Primary allowed frontend origin | `http://localhost:3000` |
| `CORS_ALLOWED_ORIGINS` | Additional comma-separated trusted origins | None |
| `STRIPE_SECRET_KEY` | Subscription and add-on billing | Optional |
| `PUBLIC_BACKEND_URL` | Public webhook origin | Optional |
| `WAHA_BASE_URL` | Private WAHA service origin | Optional |

The frontend uses `REACT_APP_API_URL` when provided and otherwise sends requests to the same-origin `/api` path.

### Voice assistant setup and manual checks

The voice screen uses the Gemini Developer API's `gemini-3.5-transcribe` model
for transcription. Sinhala responses, including recognized package terms such
as SLT-MOBITEL, Data Package, GB, Mbps, Wi-Fi, Fiber, 4G and 5G, are synthesized
locally with the `chan4lk/piper-tts-sinhala` ONNX voice. These English terms
are normalized only in the speech input; the visible/chat response is
unchanged. English and other unsupported code-mixed responses retain the
existing Gemini TTS route and voice configuration. Piper returns mono 16-bit
WAV at the model's configured 22,050 Hz sample rate. The pinned voice files and
model card are provisioned at backend image build time. The voice model is
MIT-licensed; the Piper engine is GPL-3.0-or-later. The Gemini TTS model setting
remains in use for English and unsupported mixed-language speech. Browser
speech synthesis is not used as a fallback. Gemini STT keys remain server-side.

Click **Start Conversation** once to grant microphone access and begin a
hands-free, turn-based session. Browser audio is captured as mono 16 kHz PCM WAV
directly from the microphone, avoiding compressed-recording decode errors.
Voice activity detection ignores brief noise, ends an utterance after about one
second of silence, and caps an utterance at 20 seconds. The internal transcript
is sent to chat without being displayed; after speech playback finishes, the
assistant automatically listens for the next turn. Click **Stop Conversation**
or leave the page to stop playback, cancel requests, and release microphone
resources. Automatic language identification is used unless a language is
selected manually. Gemini does not provide a confidence score through this
integration.

Gemini's current transcription model documentation describes automatic
identification across 85+ languages and code-switching, but its published
language table does not currently list Sinhala. The deployed model did return
Sinhala Unicode for a generated Sinhala test utterance; natural Sri Lankan
microphone speech still requires manual end-to-end testing. TTS documentation
does list Sinhala, but actual pronunciation, Sri Lankan accent quality, and
mixed-language prosody must be assessed by listening to generated recordings.
See Google's [audio transcription](https://ai.google.dev/gemini-api/docs/transcribe),
[TTS language support](https://ai.google.dev/gemini-api/docs/speech-generation#supported-languages),
[pricing](https://ai.google.dev/gemini-api/docs/pricing), and
[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) documentation.
Gemini 429 responses are not retried when they indicate an exhausted daily
quota. The voice session pauses and displays the provider's `Retry-After` wait
when available; check the configured project in Google AI Studio or wait for
the quota reset. The investigated project reported a Free Tier TTS limit of 10
requests per day. Limits can vary by project and tier. STT requests use only the
configured transcription model; the backend does not silently switch models.

Run real end-to-end checks with a microphone in a supported browser:

1. Select Auto, click **Start Conversation** once, and say
   **"මට මාසෙකට හොඳ data package එකක් ඕනේ."** Confirm that the response is spoken
   in Sinhala and that the screen does not show the internal transcript.
2. Ask a Sinhala follow-up without clicking Start again. Confirm the assistant
   responds with the existing conversation context and then listens again.
3. Select Auto and ask an English question. Confirm the response remains
   English and listening resumes after playback.
4. Say a mixed sentence containing **Fiber**, **Wi-Fi**, **10 GB**, and **30
   Mbps**. Listen for clear language changes in the response.
5. Test silence, background noise, microphone denial, TTS rate limiting,
   stopping during each state, and leaving the page during a turn.
6. Ask a Sinhala-speaking listener to assess Sinhala pronunciation, number and
   currency reading, pacing, and the SLT-MOBITEL brand name. Automated tests
   mock providers and cannot certify real-world pronunciation.

## Testing

Run the backend unit tests from the backend directory:

```bash
cd Backend_Agentstore
python -m unittest discover -p "test_*.py"
```

Run the frontend tests once, without watch mode:

```bash
cd chatbot
npm test -- --watchAll=false
```

Some integration paths require PostgreSQL, Qdrant, provider credentials, or mocked external services.

## Deployment

- [Production deployment and security guide](documentation/deployment.md)
- [Kubernetes setup guide](kubernetes/K8S_SETUP_GUIDE.md)
- [Kubernetes quick start](kubernetes/QUICK_START.md)
- [Authentication manual](documentation/authenticate.md)
- [WhatsApp add-on runbook](documentation/whatsapp-addon-runbook.md)
- [WAHA provider runbook](documentation/waha-adapter-runbook.md)

Keep the backend, PostgreSQL, Qdrant, and provider administration interfaces private. Terminate public HTTPS at a trusted reverse proxy or ingress and use explicit CORS origins for authenticated routes.

##
POWERED BY SLT DIGITAL LAB
