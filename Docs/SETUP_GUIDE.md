# 🛠️ Setup Guide

## Prerequisites

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/)
- **Docker Desktop** — [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
- **Git** — already installed
- **A WhatsApp account** — with a phone number you can link (like a second device)

---

## Step 1: Clone & Install Python Dependencies

```bash
cd d:\GitHub\Agentic\WhatsApp-bot

# Create virtual environment
python -m venv .venv

# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

---

## Step 2: Get Free API Keys

### Google Gemini (Primary LLM + Embeddings)
1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Click "Create API Key" → copy it
3. Free tier: 1,500 requests/day, 1M token context window

### Groq Cloud (Fast Verification LLM)
1. Go to [Groq Console](https://console.groq.com/keys)
2. Sign up (no credit card needed) → Create API Key
3. Free tier: 1,000 requests/day with Llama 3.3 70B

### Cerebras (Backup LLM)
1. Go to [Cerebras Cloud](https://cloud.cerebras.ai/)
2. Sign up → Get API key
3. Free tier: 1M tokens/day

### OpenRouter (Fallback LLM Gateway)
1. Go to [OpenRouter](https://openrouter.ai/keys)
2. Sign up → Create API Key
3. Free tier: 200 requests/day across multiple models

---

## Step 3: Configure Environment Variables

```bash
# Copy the template
copy .env.example .env

# Edit .env with your API keys
notepad .env
```

Fill in your API keys:
```
GEMINI_API_KEY=your_actual_key
GROQ_API_KEY=your_actual_key
CEREBRAS_API_KEY=your_actual_key
OPENROUTER_API_KEY=your_actual_key
```

---

## Step 4: Start Evolution API (WhatsApp Gateway)

```bash
# Start only the Evolution API container first
docker-compose up -d evolution-api

# Check it's running
docker-compose logs evolution-api
```

Evolution API should be accessible at `http://localhost:8080`.

---

## Step 5: Connect WhatsApp

### Option A: Via the Python Backend (recommended)
```bash
# Start the Python backend
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# The backend auto-creates the instance and configures webhooks on startup
# Check the QR code at:
curl http://localhost:8080/instance/connect/college-bot -H "apikey: my_secure_bot_key_2026"
```

### Option B: Manual Instance Setup
```bash
# 1. Create instance
curl -X POST http://localhost:8080/instance/create \
  -H "Content-Type: application/json" \
  -H "apikey: my_secure_bot_key_2026" \
  -d '{"instanceName": "college-bot", "integration": "WHATSAPP-BAILEYS", "qrcode": true}'

# 2. Get QR code (scan with WhatsApp → Linked Devices → Link a Device)
curl http://localhost:8080/instance/connect/college-bot \
  -H "apikey: my_secure_bot_key_2026"

# 3. Check connection
curl http://localhost:8080/instance/connectionState/college-bot \
  -H "apikey: my_secure_bot_key_2026"
```

Scan the QR code using WhatsApp on your phone:
1. Open WhatsApp → ⋮ Menu → **Linked Devices**
2. Tap **Link a Device**
3. Scan the QR code

---

## Step 6: Set Up Google Calendar (Optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g., "College Assistant")
3. Enable the **Google Calendar API**
4. Go to **APIs & Services → Credentials**
5. Create **OAuth 2.0 Client ID** (type: Desktop app)
6. Download the JSON → save as `credentials.json` in the project root
7. On first run, a browser window will open for authorization
8. After authorizing, `token.json` is saved automatically

---

## Step 7: Find Your Announcement Group JID

After connecting WhatsApp, find your announcement group's JID:

```bash
# List all groups
curl http://localhost:8000/groups
```

Copy the `jid` of your announcement group and add it to `.env`:
```
ANNOUNCEMENT_GROUP_JID=120363012345678@g.us
```

---

## Step 8: Run Everything

```bash
# Option A: Docker (both services)
docker-compose up -d

# Option B: Local development
# Terminal 1: Evolution API
docker-compose up -d evolution-api

# Terminal 2: Python backend (with auto-reload)
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Verify It Works

```bash
# Health check
curl http://localhost:8000/health

# Test direct query (without WhatsApp)
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is polymorphism in OOP?"}'

# Check schedule
curl http://localhost:8000/schedule
```

Then send a message to the linked WhatsApp number — you should get an AI response! 🎉

---

## Troubleshooting

| Issue | Solution |
|:------|:---------|
| Evolution API not starting | Check Docker is running: `docker ps` |
| QR code expired | Restart Evolution API: `docker-compose restart evolution-api` |
| WhatsApp disconnected | Re-scan QR code via `/instance/connect` endpoint |
| Gemini API error | Verify `GEMINI_API_KEY` in `.env` and check rate limits |
| Calendar auth error | Delete `token.json` and re-authorize |
| Port 8000 in use | Change `API_PORT` in `.env` or use `--port 8001` |
