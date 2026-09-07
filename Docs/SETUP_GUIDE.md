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

## Step 4: Start Evolution API & Database

```bash
# Start PostgreSQL and Evolution API containers
docker-compose up -d evolution-postgres evolution-api

# Check that containers are healthy
docker-compose ps
```

Evolution API will be accessible at `http://localhost:8080`.

---

## Step 5: Connect WhatsApp

### Easy Browser Pairing (Recommended)
1. Start the Python backend:
   ```bash
   docker-compose up -d python-backend
   ```
2. Open your browser to **[http://localhost:8000/qr](http://localhost:8000/qr)** (or check `qr_code.png` in the repo).
3. On your phone:
   - Open WhatsApp &rarr; **Settings** (or **⋮ Menu**) &rarr; **Linked Devices**
   - Tap **Link a Device**
   - Point your camera at the QR code on your screen.
4. As soon as you scan, the interface will automatically switch to **✅ WhatsApp Connected!**
   - You can also verify by visiting `http://localhost:8000/health`.
   - If the code expires, click the **"🔄 Get Fresh QR Code"** button.

---

## Step 6: Set Up Google Calendar

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., "College Assistant").
3. Enable the **Google Calendar API**.
4. Go to **APIs & Services → Credentials**:
   - Click **Create Credentials** &rarr; **OAuth client ID**.
   - Application type: **Desktop app**.
   - Download the JSON file and rename it to `credentials.json` in your project root.
5. Generate your OAuth token:
   ```bash
   # Run the calendar authorization helper
   python setup_calendar.py
   ```
   A browser window will open asking you to sign in with your Google Account and grant Calendar access. This creates `token.json`.
6. *(Important)* **Multi-User Shared Calendar (Option 1)**:
   - Create a secondary calendar in Google Calendar (e.g. "College Exams & Labs").
   - Under **Settings and sharing** for that calendar, copy its **Calendar ID** (e.g. `xyz...@group.calendar.google.com`).
   - Add it to your `.env`:
     ```bash
     GOOGLE_CALENDAR_ID=xyz...@group.calendar.google.com
     ```
   - Share the public or view link with your students so everyone sees the updates automatically!
7. **Security Note**:
   - `credentials.json` and `token.json` contain sensitive OAuth secrets and are permanently excluded by `.gitignore`. **Never commit them to GitHub.**

---

## Step 7: Configure Announcement Group & Webhook

After connecting WhatsApp, list the groups your bot has joined:

```bash
curl http://localhost:8000/groups
```

Copy the `jid` of your target announcement group and set it in `.env`:
```
ANNOUNCEMENT_GROUP_JID=120363410586240165@g.us
```

Whenever `.env` is modified, reload the backend:
```bash
docker-compose up -d python-backend
```

---

## Step 8: Verification & Bot Commands

### System Health
```bash
curl http://localhost:8000/health
```

### Direct Query (Test without WhatsApp)
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"What is polymorphism in OOP?\"}"
```

### Schedule Verification
```bash
curl http://localhost:8000/schedule
```

### WhatsApp Commands
| Action | Message / Command | Description |
|:-------|:------------------|:------------|
| **View Schedule** | `/schedule` or `جدول` | Returns upcoming exams/labs from the shared calendar |
| **Delete Single Event** | `/delete [Subject]` or `احذف امتحان [المادة]` | Removes matching event from calendar (bilingual matching) |
| **Bulk Delete** | `delete all exam schedule` or `احذف كل الامتحانات` | Clears all events in the semester window |
| **Add Timetable Image** | Send photo of timetable (DM or Group) | AI parses subjects, dates, and times and syncs to Google Calendar |
| **Ask Question** | Send any study question or photo | Generates an AI answer with citations |

---

## Troubleshooting

| Issue | Cause | Solution |
|:------|:------|:---------|
| **"Couldn't link device"** | Stale session or device limit | In WhatsApp on your phone, check **Linked Devices** (max 4). On `http://localhost:8000/qr`, click **"🔄 Get Fresh QR Code"** and scan immediately. |
| **Webhook not received** | Docker bridge routing | Evolution API inside Docker must forward to `http://python-backend:8000/webhook`, NOT `host.docker.internal`. `src/api/main.py` configures this automatically on boot. |
| **Gemini 429 Quota Exceeded** | `gemini-2.5-flash` daily limit (20 RPD) | The system uses `src/agents/llm_router.py` which prioritizes `gemini-2.5-flash-lite` (1,500 RPD) and falls back to Groq Cloud automatically. |
| **Calendar deletion not matching** | Language or timeframe mismatch | The updated `delete_events` engine searches `[-30d, +180d]`, uses English-Arabic subject translation, and falls back to LLM matching. |
| **Port 8000 in use** | Port conflict | Adjust `API_PORT` in `.env` and `docker-compose.yml`. |
