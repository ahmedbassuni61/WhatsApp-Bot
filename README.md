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
- **Enforced Structured Schedule Output**: Always returns an aesthetically structured timetable with day headers, exact start and end times (`11:30 → 12:30`), hall locations (`📍 مدرج 3`), and universal high-visibility emojis (`📚 [LECTURE]`, `🔬 [LAB]`, `📝 [EXAM]`, `👥 [SECTION]`, `⏰ [DEADLINE]`).
- **Multi-Session Same-Day Intelligence**: Accurately recognizes and schedules multiple lecture or lab periods of the same subject on the same day without false duplicate skipping.
- **Intelligent Conflict Detection**: Identifies genuine room or time clashes between distinct courses, filtering out self-copies, and cleanly appends bilingual warnings at the very end of schedule additions.
- **Verbatim Message Preservation**: The student's exact WhatsApp message or announcement text is saved verbatim into the calendar event's `description` field for full context.
- **Shared Multi-User Calendar**: Syncs to a dedicated shared secondary Google Calendar. Unlimited students and faculty can subscribe via calendar link without individual OAuth logins.
- **Intelligent Deletion**: Supports natural language deletion (`/delete math`, `احذف امتحان الرياضيات`) and bulk clearing (`delete all exam schedule`) with cross-language subject aliases and LLM fallback.
- **Instant Schedule Query**: Check upcoming exams anytime with `/schedule` or `جدول`.

### 📁 Google Drive Academic Materials Explorer & Search
- **Dynamic Folder Exploration**: The bot dynamically lists folders, subjects, and subdirectories (`list_drive_folder`), allowing students to explore course materials, lectures, and sections on any drive structure without hardcoded paths.
- **Multi-Step Subject Inquiries**: Handles multi-step requests like "How many lectures in Digital Communications?" or "كام محاضرة في المادة؟" by autonomously navigating into course subfolders, counting files, and providing direct Drive links.
- **Semantic Drive Search**: Find specific exam slides, summaries, or lectures with `search_drive`.

### ⏰ Timezone & Relative Time Intelligence (`src/tools/time_tool.py`)
- **Deterministic Time Math**: Resolves relative time phrases ("tomorrow at 3pm", "كمان ساعتين", "Sunday next week", "بعد بكرة") mathematically with timezone awareness (`Africa/Cairo`), ensuring zero date/time hallucination.

### 🧠 LangGraph StateGraph & Adversarial Reflection Loop (`src/agents/graph.py`, `reflector.py`)
- **5-Node StateGraph Architecture**: Execution is governed by a compiled LangGraph state machine featuring `router`, `executor`, `reflector`, `committer`, and `respond_node`.
- **Adversarial Reflection Quality Gate**: An active critic reviews answers before delivery to catch cross-column contamination from timetable images, auto-repair duplicates, and swap inverted times in place.
- **Per-User Memory**: Context-aware conversation history per user JID so follow-up queries retain context.

### ⚡ Unified Multi-LLM Gateway (`src/agents/llm_router.py`)
- **Default Priority on Gemini 3.5 Flash Lite**: Optimized priority queue starting with `gemini-3.5-flash-lite`, followed by `gemini-3.5-flash`, `gemini-2.5-flash`, and `gemini-2.5-flash-lite`.
- **Extended Multimodal Timeouts**: Dedicated 50s execution windows for large vision payloads with transient 45s cooldowns to preserve high-tier availability.
- **Single-Inference Responses**: General study questions, concept explanations, and homework help are answered directly in a single LLM pass (~1.2s), cutting API calls and latency in half.
- **Automatic Quota Failover**: Seamlessly fails over across Google Gemini and Groq Cloud (`llama-3.3-70b-versatile`, `mixtral-8x7b-32768`) with automatic cooldown tracking.
- **Model Caching & Optimization**: Pre-caches model connections in memory and automatically downscales/compresses camera images from 10MB+ down to ~100KB JPEG before sending to the model.
- **Combined Capacity**: ~3,500+ free queries per day across providers.

### 📱 WhatsApp Gateway & Real-Time Presence
- **Zero-Chromium Architecture**: Built on Dockerized Evolution API v2 wrapping the Baileys WebSocket protocol.
- **Interactive Browser Pairing**: Scan the QR code at `http://localhost:8000/qr` with real-time status detection, auto-timer, and one-click refresh.
- **Synchronized Typing Presence**: WhatsApp `composing` indicator starts immediately upon message arrival, giving natural visual feedback while the model processes, and replies are dispatched instantly with zero artificial delay.

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
          ┌───────────────────────┼───────────────────────┬───────────────────────┐
          ▼                       ▼                       ▼                       ▼
 ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │  LLM Router      │   │ Calendar Sync    │   │ Drive Client &   │   │ Time & Memory    │
 │  Gemini + Groq   │   │ Google Cal API   │   │ Dynamic Indexer  │   │ Timezone + Context
 └──────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────────┘
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

### 4. Connect Google APIs (Calendar & Drive)
```bash
# Authorize Google Calendar & Drive (opens browser consent window)
python setup_google.py
```
Add your shared calendar ID and Google Drive folder ID to `.env`:
```env
GOOGLE_CALENDAR_ID=your_calendar_id@group.calendar.google.com
GOOGLE_DRIVE_FOLDER_ID=your_drive_root_folder_id
```

---

## 💬 WhatsApp Commands & Usage

| Command / Trigger | Example | Description |
|:------------------|:--------|:------------|
| **View Schedule** | `/schedule` or `جدول` | Lists all upcoming exams, labs, and deadlines |
| **Add / Remind Event** | `Remind me tomorrow at 3pm to study for digital communications quiz` | Calculates exact datetime, adds event to Google Calendar, and saves your verbatim message in the event description |
| **Send Timetable Image** | *(Attach photo of schedule)* | AI extracts subjects, dates, and times and syncs directly to Google Calendar |
| **Delete Single Event** | `/delete math` or `احذف امتحان الرياضيات` | Deletes matching event from Google Calendar |
| **Bulk Clear Schedule** | `delete all exam schedule` or `احذف كل الامتحانات` | Clears all events in the semester window |
| **Explore Subjects & Folders** | `list subjects` or `وريني المواد اللي عندك` | Dynamically navigates root Google Drive folders and lists available subjects |
| **Inspect Folder Contents** | `open lectures folder` or `كام محاضرة في مادة الاتصالات؟` | Traverses course directories, counts lectures/sections, and outputs direct Drive links |
| **Search Course Materials** | `find transmission media midterm` or `search slides` | Searches Drive index for matching PDFs, slides, and study notes |
| **Ask Study Question** | `What is inheritance in OOP?` | Answers using multi-LLM reasoning in a single pass |

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
│   ├── agents/               # LLM router, agentic tool loop, and memory
│   │   ├── agent.py          # Message orchestration & multi-step tool-calling loop
│   │   ├── llm_router.py     # Unified Gemini + Groq gateway with quota tracking
│   │   ├── memory.py         # Per-user conversational memory buffer
│   │   └── tools.py          # Calendar tools, timetable parser, & Drive browser
│   ├── api/                  # FastAPI web server and routes
│   │   ├── main.py           # Webhook receiver, /qr dashboard, /drive & /schedule APIs
│   │   └── models.py         # Pydantic schemas
│   ├── drive/                # Google Drive exploration & indexing
│   │   ├── drive_client.py   # Async Google Drive v3 client
│   │   └── drive_indexer.py  # SQLite cache and dynamic Drive crawler
│   ├── database/             # Vector store (ChromaDB)
│   ├── export/               # Answer-to-image and PDF export
│   ├── ingestion/            # PDF, audio, and video RAG processors
│   ├── retrieval/            # Multi-collection semantic search
│   ├── tests/                # Automated unit tests (pytest)
│   │   ├── test_agentic_loop.py # Agentic tool-calling loop tests
│   │   ├── test_drive_search.py # Google Drive search & crawler tests
│   │   ├── test_llm_router.py   # LLMRouter failover and model tests
│   │   ├── test_memory.py       # Conversational memory tests
│   │   └── test_time_tool.py    # Timezone and relative time tests
│   ├── tools/                # Time intelligence & deterministic math
│   │   └── time_tool.py      # Relative time parser & Cairo timezone context
│   └── whatsapp/             # WhatsApp integration
│       ├── bot.py            # Message routing & synchronized typing presence
│       ├── calendar_sync.py  # Non-blocking Google Calendar sync & delete engine
│       ├── evolution_client.py # Evolution API v2 async REST client
│       └── group_listener.py # Multimodal announcement & timetable parser
├── docker-compose.yml        # Multi-container orchestration
├── Dockerfile                # Python backend container (with pytest)
├── pyproject.toml            # Project metadata & pytest configuration
├── requirements.txt          # Python dependencies
└── setup_google.py           # Google Calendar & Drive OAuth initialization
```

### 🧪 Automated Testing
Run the comprehensive test suite locally or inside Docker:
```bash
pytest -v
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
