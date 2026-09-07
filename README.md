# 🎓 College Assistant AI (WhatsApp Bot)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Zero Cost](https://img.shields.io/badge/Hosting%20%26%20APIs-%240%2Fmonth-success.svg)](#zero-cost-philosophy)

An agentic, multi-modal **College Assistant AI** accessible via WhatsApp. It bridges lecture materials, exam timetables, study questions, and announcement groups with a fully autonomous, zero-cost AI backend.

---

## 🌟 Key Features

### 📅 Multimodal Timetable Parsing & Shared Google Calendar
- **AI Timetable OCR**: Send a photo of complex university exam schedules (Arabic or English). The bot parses all dates, subjects, times, and halls, and adds them directly to Google Calendar.
- **Shared Multi-User Calendar (Option 1)**: Syncs to a dedicated shared secondary Google Calendar. Unlimited students and faculty can subscribe via calendar link without individual OAuth logins.
- **Intelligent Deletion**: Supports natural language deletion (`/delete math`, `احذف امتحان الرياضيات`) and bulk clearing (`delete all exam schedule`) with cross-language subject aliases and LLM fallback.
- **Instant Schedule Query**: Check upcoming exams anytime with `/schedule` or `جدول`.

### ⚡ Resilient Multi-LLM Router (`src/agents/llm_router.py`)
- **Automatic Failover**: Seamlessly switches between Google Gemini (`gemini-2.5-flash-lite`, `gemini-flash-latest`, `gemini-3.5-flash-lite`) and Groq Cloud (`llama-3.3-70b-versatile`, `qwen/qwen3.8-27b`, `allam-2-7b`).
- **Quota Protection**: Prioritizes Gemini Flash Lite (1,500 requests/day, multimodal vision) to eliminate Google AI Studio 429 quota exhaustion.
- **Combined Capacity**: ~3,500+ free queries per day across providers.

### 📱 WhatsApp Gateway (Evolution API v2)
- **Zero-Chromium Architecture**: Built on Dockerized Evolution API v2 wrapping the Baileys WebSocket protocol.
- **Interactive Browser Pairing**: Scan the QR code at `http://localhost:8000/qr` with real-time status detection, auto-timer, and one-click refresh.
- **Humanized Anti-Ban Presence**: Automated typing indicators (`composing`) with randomized jitter delays (1.5s - 4.0s).

### 📚 Multi-Source RAG & LangGraph Reasoning *(In Progress)*
- **Data Ingestion**: Process lecture PDFs, extract textbook diagrams, and transcribe lecture audio/video with local Whisper.
- **ChromaDB Vector Store**: Semantic retrieval across lectures, previous exams, and transcripts.
- **LangGraph Verification**: Multi-LLM consensus verification before answering student queries.

---

## 🏛️ System Architecture

```
                    ┌─────────────────────────┐
                    │      WhatsApp User      │
                    │   (Direct or Group)     │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Evolution API v2      │
                    │   (Baileys / Docker)    │
                    └────────────┬────────────┘
                                 │ Webhook (POST /webhook)
                    ┌────────────▼────────────┐
                    │    FastAPI Backend      │
                    │  (Port 8000 / Docker)   │
                    └────────────┬────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  LLM Router      │   │ Calendar Sync    │   │ ChromaDB Vector  │
│  Gemini + Groq   │   │ Google Cal API   │   │ Store (RAG)      │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

---

## 🚀 Quick Start Guide

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [Python 3.10+](https://www.python.org/downloads/)
- A WhatsApp account for the bot (e.g. secondary number or linked device)

### 1. Clone & Configure Environment
```bash
git clone https://github.com/ahmedbassuni61/WhatsApp-Bot.git
cd WhatsApp-Bot

# Copy environment variables template
copy .env.example .env
```

Edit `.env` with your API keys:
- `GEMINI_API_KEY`: From [Google AI Studio](https://aistudio.google.com/apikey) (Free)
- `GROQ_API_KEY`: From [Groq Console](https://console.groq.com/keys) (Free)

### 2. Start the Docker Stack
```bash
docker-compose up -d
```
This boots three containers:
- `college_assistant` (FastAPI backend on port 8000)
- `evolution_api` (WhatsApp REST gateway on port 8080)
- `evolution_postgres` (Session state storage on port 5432)

### 3. Link WhatsApp via Browser
1. Open **[http://localhost:8000/qr](http://localhost:8000/qr)** in your browser.
2. In WhatsApp on your phone: **Settings → Linked Devices → Link a Device**.
3. Scan the QR code. The browser updates to **✅ WhatsApp Connected!** immediately.

### 4. Connect Google Calendar
```bash
# Authorize with Google Calendar (opens browser window)
python setup_calendar.py
```
Add your shared calendar ID to `.env`:
```env
GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com
```

---

## 💬 WhatsApp Commands & Usage

| Command / Trigger | Example | Description |
|:------------------|:--------|:------------|
| **View Schedule** | `/schedule` or `جدول` | Lists all upcoming exams and events |
| **Send Timetable Image** | *(Attach photo of schedule)* | AI extracts all subjects, dates, and times and syncs to Google Calendar |
| **Delete Single Event** | `/delete math` or `احذف امتحان الرياضيات` | Deletes matching event from Google Calendar |
| **Bulk Clear Schedule** | `delete all exam schedule` or `احذف كل الامتحانات` | Clears all events in the semester window |
| **Ask Study Question** | `What is inheritance in OOP?` | Answers using multi-LLM reasoning |

---

## 📂 Project Structure

```
WhatsApp-bot/
├── Docs/                     # Detailed architectural and setup guides
│   ├── ARCHITECTURE.md       # Full system architecture and data flow
│   ├── PROJECT_OVERVIEW.md   # Project scope, features, and CV impact
│   ├── SETUP_GUIDE.md        # Comprehensive local & cloud setup walkthrough
│   └── TECH_STACK.md         # Detailed breakdown of zero-cost technologies
├── src/
│   ├── agents/               # LLM router and LangGraph agents
│   │   └── llm_router.py     # Gemini + Groq multi-LLM failover router
│   ├── api/                  # FastAPI web server and routes
│   │   ├── main.py           # Webhook receiver, /qr dashboard, /schedule API
│   │   └── models.py         # Pydantic schemas
│   ├── database/             # Vector store (ChromaDB)
│   ├── export/               # Answer-to-image and PDF export
│   ├── ingestion/            # PDF, audio, and video RAG processors
│   ├── retrieval/            # Multi-collection semantic search
│   └── whatsapp/             # WhatsApp integration
│       ├── bot.py            # Message routing & anti-ban typing delay
│       ├── calendar_sync.py  # Google Calendar sync, multi-user, delete engine
│       ├── evolution_client.py # Evolution API v2 async REST client
│       └── group_listener.py # Multimodal announcement & timetable parser
├── docker-compose.yml        # Multi-container orchestration
├── Dockerfile                # Python backend container
├── requirements.txt          # Python dependencies
└── setup_calendar.py         # Google Calendar OAuth initialization
```

---

## 📖 In-Depth Documentation

For step-by-step guides and deep dives into the system design, see:
- [📘 Setup Guide](Docs/SETUP_GUIDE.md) — Prerequisites, API keys, Calendar OAuth, and troubleshooting.
- [🏛️ System Architecture](Docs/ARCHITECTURE.md) — Data flow, LangGraph states, and Evolution API bridging.
- [🛠️ Tech Stack & Quotas](Docs/TECH_STACK.md) — Detailed quota breakdown and zero-cost strategy.
- [🎯 Project Overview](Docs/PROJECT_OVERVIEW.md) — Vision, problem statement, and engineering skills.

---

## 🔒 Security Notice

- `credentials.json` and `token.json` are strictly excluded from version control via `.gitignore`.
- `.env` containing API keys and database credentials is never committed.
- Always use the provided `.env.example` as a template.

---

## 📜 License

This project is licensed under the [MIT License](LICENSE).
