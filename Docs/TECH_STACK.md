# 🛠️ Tech Stack — Detailed Breakdown

Every technology in this project was selected for **zero cost** operation at meaningful scale.

---

## 1. Multi-LLM Router Architecture (`src/agents/llm_router.py`)

A centralized, unified multi-LLM gateway with model pooling, memory caching, and automatic failover across free-tier providers.

### Primary Multimodal & Tool Reasoning: Google Gemini
- **`gemini-3.5-flash-lite`** (Default Priority Model):
  - Ultra-fast latency, high multimodal accuracy for timetable image OCR, and generous daily request limits. Configured with a 50s multimodal timeout for vision-heavy timetable analyses and a 45s transient cooldown to quickly bypass momentary upstream hiccups.
- **`gemini-3.5-flash`**:
  - High-tier multimodal reasoning fallback for complex parsing tasks.
- **`gemini-2.5-flash` & `gemini-2.5-flash-lite`**:
  - Secondary multimodal failovers when higher tiers hit quotas or rate limits.

### Text Fallback & Verification: Groq Cloud
- **Models**:
  - `llama-3.3-70b-versatile` (70B parameter open-weights leader)
  - `mixtral-8x7b-32768` (fast mixture-of-experts fallback)
- **Free Limits**: 30 RPM, 1,000 RPD (no credit card required)
- **Speed**: 300-800 tokens/second on custom LPUs.
- **Role**: Text Q&A fallback and tool-calling when vision is not required.

### Performance & Latency Optimizations
- **Single-Inference Responses**: General study questions and conversations are answered directly in 1 LLM pass (~1.2s), bypassing unnecessary tool call redirection.
- **Model Object Caching**: Models are pre-initialized in memory inside `LLMRouter.__init__()`, eliminating object construction overhead on incoming messages.
- **Image Compression**: Automatically downscales high-res photos to max 1024px and encodes as JPEG with quality=85, cutting payloads from 10MB+ down to ~100KB.
- **Non-Blocking Calendar API**: Runs Google Calendar `.execute()` calls in `asyncio.to_thread` with `cache_discovery=False`.

### Combined Daily Free Capacity
| Provider & Model Tier | Daily Requests | Primary Capability |
|:----------------------|:---------------|:-------------------|
| Gemini 3.5 / 2.5 Flash & Flash Lite | ~1,500 RPD | Multimodal vision, timetable OCR, primary Q&A |
| Groq (Llama 3.3 70B / Mixtral) | ~1,000 RPD | Fast text generation, tool calling fallback |
| Cerebras / OpenRouter | ~500-1,000 RPD | Backup text endpoints |
| **Total Guaranteed Free** | **~3,000-3,500+ / day** | Completely $0/month |

---

## 2. Vector Database: ChromaDB

- **Type**: Local / embedded (SQLite + Parquet backend)
- **Cost**: $0 forever (open source)
- **Capacity**: Unlimited (constrained only by disk space)
- **Why chosen over alternatives**:
  - **vs Pinecone**: Pinecone free tier has 2GB storage and egress limits
  - **vs Qdrant Cloud**: Free cluster suspends after 1 week inactivity, deletes after 4 weeks
  - **vs Weaviate Cloud**: Limited to 100k objects
  - **vs FAISS**: ChromaDB adds metadata filtering, persistence, and collection management on top of raw vector search
  - **vs Supabase pgvector**: Good alternative if we need relational data too, but ChromaDB is simpler for pure vector search

### Collections Design
```
chroma_db/
├── lectures/          # PDF lecture content chunks
├── exams/             # Previous exam Q&A
├── transcripts/       # Video/audio transcripts
└── images/            # Image descriptions with file path references
```

---

## 3. Embedding Model: FastEmbed

- **Model**: `BAAI/bge-small-en-v1.5` (384 dimensions, 512 token context)
- **Runtime**: ONNX (no PyTorch dependency, <50MB RAM)
- **Speed**: 2-5ms per embedding on CPU
- **Cost**: $0 forever (runs locally)
- **Why chosen**:
  - No rate limits (vs Google Embedding API's 1,500 RPD)
  - No network latency
  - Top-tier MTEB retrieval benchmark scores
  - If Arabic content needed: swap to `paraphrase-multilingual-MiniLM-L12-v2`

---

## 4. Transcription: OpenAI Whisper

- **Model**: `whisper-base` or `whisper-small` (local)
- **Cost**: $0 forever (open source, runs locally)
- **Languages**: Supports 99 languages including Arabic and English
- **Why chosen**: State-of-the-art accuracy, handles accented speech, can be run on CPU
- **Alternative**: `faster-whisper` (CTranslate2 backend, 4x faster, same accuracy)

---

## 5. Agentic Framework: LangGraph

- **Cost**: $0 (open source)
- **Why LangGraph over plain LangChain**:
  - Explicit **state machine** with nodes and edges (vs implicit chain).
  - Supports **cycles** (adversarial reflection & auto-repair loop).
  - Built-in **human-in-the-loop** (user confirmation before image generation).
  - Observable and debuggable execution graphs.
  - First-class support for multi-agent architectures.

### The 5-Node StateGraph Architecture (`src/agents/graph.py`)

```
[User Message / Image]
         │
         ▼
    ┌──────────┐    tool_calls     ┌────────────┐
    │  Router  │ ─────────────────►│  Executor  │
    │  (LLM)   │ ◄─────────────────│  (Tools)   │
    └────┬─────┘    tool_output    └────────────┘
         │
         │ final text answer / completed tools
         ▼
    ┌──────────┐  fail (auto-repair / re-query)
    │Reflector │ ─────────────────────────────────┐
    │ (Critic) │                                  │
    └────┬─────┘                                  ▼
         │ pass                           (Back to Router)
         ▼
    ┌──────────┐                   ┌──────────────┐
    │Committer │ ─────────────────►│ Respond Node │
    │(CalSync) │  committed events │ (Structured) │
    └──────────┘                   └──────────────┘
```

1. **`router` (LLM Reasoning)**: Evaluates user intent, conversation history, and available tools. Directs execution to tool invocation or produces conversational responses.
2. **`executor` (Tool Execution)**: Runs calendar sync, timetable OCR, Drive operations, or RAG search. Captures candidate calendar events into `pending_events` rather than committing blindly.
3. **`reflector` (Adversarial Reflection & Verification Gate)**:
   - **Deterministic Guardrails**: Instant validation of dates (ISO YYYY-MM-DD, $\le 365$ days ahead), time boundaries (start < end), and auto-repair for inverted slots.
   - **Vision & Timetable Verification**: Deeply inspects physical timetable columns (e.g. `8:30-9:30` ... `19:30-20:30`) against the original image to verify subjects, avoid cross-column contamination, and differentiate back-to-back lectures from conflicts.
   - **Loop Control**: Allows up to 2 correction cycles with explicit critique injection, then fails open to prevent infinite loops.
4. **`committer` (Sanitized Commit & Non-Blocking Conflict Detection)**:
   - Commits validated events to Google Calendar.
   - Applies **multi-session same-day deduplication** (preserves distinct lecture/lab periods of the same course on the same date).
   - Detects true conflicts between different courses (excluding self-copies and all-day notes).
5. **`respond_node` (Enforced Structured Output)**:
   - Formats timetable responses into a clean, day-grouped schedule with exact start and end times (`11:30 → 12:30`), locations (`📍 مدرج 3`), and universal academic emojis (`📚`, `🔬`, `📝`, `👥`, `⏰`, `💬`, `📌`).
   - Appends bilingual conflict warnings at the very end of the message when clashes are detected.

---

## 6. WhatsApp: Baileys / Evolution API

### Implemented Architecture: Evolution API v2 (Docker + PostgreSQL)
- **Engine**: Self-hosted Evolution API v2 wrapping Baileys WebSocket protocol.
- **Database**: PostgreSQL 15 (`evolution_postgres`) for persistent Multi-Device authentication state.
- **Networking**: Runs in Docker network `college-net`. Webhook routes directly to `http://python-backend:8000/webhook`.
- **Key Features**:
  - Full REST API client (`src/whatsapp/evolution_client.py`).
  - Base64 decrypted media extraction for images, audio, and documents.
  - Humanized typing indicators (`composing`, jittered delay between 1.5s - 4.0s) to minimize ban risk.
  - Interactive browser pairing at `/qr` with auto-refresh and instant connection detection.

---

## 7. Hosting: Oracle Cloud Always Free

- **Compute**: 4 Ampere ARM vCPUs + 24GB RAM
- **Storage**: 200GB block storage
- **Network**: 10TB/month egress, static IPv4
- **Uptime**: 24/7 always on (never sleeps, never expires)
- **Cost**: $0 forever (requires credit card for verification only)
- **Why chosen**: Enough resources to run the entire stack (Python backend + WhatsApp gateway + Postgres + ChromaDB + Whisper) on a single VM

---

## 8. APIs & Integrations

### Google Calendar API
- **Cost**: Free
- **Authentication**: OAuth 2.0 with `token.json` (auto-refreshes).
- **Multi-User Sharing**: Supports writing to a dedicated shared secondary calendar ID (`GOOGLE_CALENDAR_ID` in `.env`), enabling unlimited students to view updates without requiring individual OAuth credentials.
- **Security**: `credentials.json` and `token.json` are excluded from Git via `.gitignore`.
- **Engine**:
  - Auto-creates color-coded events with reminders (1 hour and 15 mins prior).
  - Preserves original WhatsApp messages or announcement text verbatim in the event's `description`.
  - **Multi-Session Same-Day Support**: `_is_duplicate` validates `title`, `date`, and `time_start`, accurately distinguishing back-to-back classes or morning/afternoon sessions of the same course.
  - **Intelligent Conflict Resolver**: Cross-checks new events against existing schedules and current batches, normalizing tags (`[LECTURE]`, `[LAB]`, `محاضرة`, `معمل`) to eliminate false self-conflicts.
  - Intelligent deletion: bulk clearing, cross-language English ↔ Arabic translation, and LLM matching fallback.

### Google Drive API v3 (`src/drive/`)
- **Cost**: Free
- **Capabilities**:
  - **Dynamic Folder Explorer**: `list_drive_folder` navigates any drive hierarchy on the fly, listing folders, subjects, lecture slides, and sections without hardcoded paths.
  - **Keyword & Topic Search**: `search_drive` indexes file metadata into a local SQLite database and performs sub-second matching on lecture titles and course codes.
  - **Combined OAuth**: Authenticated alongside Calendar via `setup_google.py` using `drive.readonly` scope.

### Timezone & Relative Time Intelligence (`src/tools/time_tool.py`)
- **Cost**: Free (standard Python standard library)
- **Engine**: Evaluates relative time phrases ("tomorrow at 3pm", "Sunday next week", "كمان ساعتين", "بعد بكرة") mathematically with timezone awareness (`Africa/Cairo`), ensuring zero date hallucination by the LLM.

### Conversational Memory (`src/agents/memory.py`)
- **Engine**: In-memory per-user sliding window history keyed by sender WhatsApp JID, enabling natural multi-turn conversations.

---

## 9. Additional Libraries

| Library | Purpose | Cost |
|:--------|:--------|:-----|
| **FastAPI** | Async Python web framework | $0 |
| **PyMuPDF (fitz)** | PDF text and image extraction | $0 |
| **Pillow** | Image processing and compression | $0 |
| **WeasyPrint** | HTML/CSS to PDF conversion | $0 |
| **LangChain** | Tool calling, schema definitions | $0 |
| **FFmpeg** | Video/audio processing | $0 |
| **pytest** | Testing framework | $0 |
| **Docker** | Containerization | $0 |
