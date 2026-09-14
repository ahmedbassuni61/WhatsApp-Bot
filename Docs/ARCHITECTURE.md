# 🏗️ System Architecture

## High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                              │
│  Google Drive  ←→  PDFs  |  Videos  |  Audio  |  Announcements  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ (auto-sync or manual upload)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     INGESTION PIPELINE                           │
│                                                                  │
│  PDF Processor ──→ Text Chunks ──→ Embeddings ──→ ChromaDB      │
│  (PyMuPDF+OCR)     (Semantic)      (FastEmbed)    (lectures)    │
│                                                                  │
│  Video Processor ─→ Transcript ──→ Embeddings ──→ ChromaDB      │
│  (FFmpeg+Whisper)   Chunks          (FastEmbed)   (transcripts) │
│                  └→ Key Frames ──────────────────→ Image Store   │
│                                                                  │
│  Audio Processor ─→ Transcript ──→ Embeddings ──→ ChromaDB      │
│  (Whisper)          Chunks          (FastEmbed)   (transcripts) │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      VECTOR STORES                               │
│                                                                  │
│  ChromaDB Collections:                                           │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ ┌──────────┐        │
│  │ lectures │ │  exams   │ │ transcripts │ │  images  │        │
│  └──────────┘ └──────────┘ └─────────────┘ └──────────┘        │
│                                                                  │
│  Image Store: ./data/images/{source_file}/{frame_001.png, ...}  │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   LANGGRAPH AGENT CYCLE                          │
│                                                                  │
│  ┌───────────┐    ┌────────────┐    ┌──────────────────┐        │
│  │   Query   │───→│  Retrieve  │───→│  Multi-LLM       │        │
│  │  Router   │    │  Context   │    │  Verification    │        │
│  └───────────┘    └────────────┘    │  ┌─────────────┐ │        │
│                                      │  │ Gemini      │ │        │
│  ┌───────────┐    ┌────────────┐    │  │ Groq/Llama  │ │        │
│  │  Export   │←───│  Answer    │←───│  │ Cerebras    │ │        │
│  │  Image    │    │  Composer  │    │  └─────────────┘ │        │
│  └───────────┘    └────────────┘    └──────────────────┘        │
│       ▲                                                          │
│       │ (only after user confirms)                               │
└───────┼─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                    WHATSAPP INTERFACE                             │
│                                                                  │
│  ┌──────────────┐    ┌───────────────┐    ┌────────────────┐    │
│  │ Evolution    │    │  FastAPI      │    │  Calendar      │    │
│  │ API (Docker) │←──→│  Backend     │───→│  Sync          │    │
│  │ (WhatsApp)   │    │  (Python)     │    │  (Google Cal)  │    │
│  └──────┬───────┘    └───────────────┘    └────────────────┘    │
│         │                                                        │
│  ┌──────┴───────┐                                                │
│  │ Student DM   │  ← Q&A bot                                    │
│  │ Announce Grp │  ← Schedule listener                          │
│  └──────────────┘                                                │
└─────────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. Ingestion Pipeline (`src/ingestion/`)

**Responsibility**: Transform raw files into searchable vector embeddings.

| Component | Input | Output | Key Libraries |
|:----------|:------|:-------|:--------------|
| `pdf_processor.py` | PDF files | Text chunks + extracted images | PyMuPDF, Tesseract |
| `video_processor.py` | Video files (mp4, mkv) | Transcript chunks + key frames | FFmpeg, Whisper |
| `audio_processor.py` | Audio files (mp3, wav, m4a) | Transcript chunks | Whisper |
| `chunker.py` | Raw text | Semantic chunks (512-1024 tokens) | LangChain TextSplitter |
| `embedder.py` | Text chunks | 384-dim vector embeddings | FastEmbed |
| `drive_sync.py` | Google Drive folder | Downloaded files | Google Drive API |

### 2. Database Layer (`src/database/`)

**Responsibility**: Store and retrieve vector embeddings with metadata.

- **ChromaDB** collections with metadata fields:
  - `source_file`: original filename
  - `content_type`: "lecture" | "exam" | "transcript"
  - `course`: course name/code
  - `page_number` / `timestamp`: location in source
  - `chunk_index`: position within the document

### 3. Retrieval Layer (`src/retrieval/`)

**Responsibility**: Find the most relevant content for a query.

- Multi-collection search (lectures + exams + transcripts)
- Reciprocal Rank Fusion for merging results from different collections
- Cross-encoder re-ranking for precision
- Image lookup: when a transcript chunk matches, fetch the corresponding video frame

### 4. Agent Layer (`src/agents/`)

**Responsibility**: Unified multi-provider routing, multi-step tool execution loop, and conversational memory.

- **`llm_router.py`**:
  - Centralized LLM gateway managing cached LangChain model pools with automatic quota failover and cooldown tracking.
  - Failover sequence: Google Gemini (`gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`) → Groq Cloud (`llama-3.3-70b-versatile`, `mixtral-8x7b-32768`).
  - Automatic image optimization: downscales large camera photos to 1024px JPEG (~100KB) to minimize network payload.
- **`agent.py`**:
  - Autonomous agent loop: executes multi-step tool calling (up to 8 iterations) when inspecting Drive directories, checking schedules, or adding events.
  - Answers general study/academic questions and greetings directly in a single inference pass (~1.2s).
- **`memory.py`**:
  - Per-user conversation memory buffer keyed by sender JID, retaining previous turns so students can ask contextual follow-up questions.
- **`tools.py`**:
  - Pydantic-typed tools:
    - `view_schedule`: query upcoming Google Calendar events.
    - `add_calendar_event`: schedule events with the student's verbatim WhatsApp message saved in the event description.
    - `delete_calendar_event`: keyword or bulk event deletion.
    - `parse_timetable_image`: multimodal schedule and timetable OCR.
    - `list_drive_folder`: dynamically inspect any Drive folder, browse subjects, count lectures, and get direct links.
    - `search_drive`: search indexed Drive study materials by topic or filename.

### 5. Google Drive Materials Layer (`src/drive/`)

**Responsibility**: Dynamic folder exploration, metadata indexing, and study material discovery.

- **`drive_client.py`**:
  - Async wrapper around Google Drive API v3.
  - Fetches folder trees, file metadata, MIME types, and web view links.
- **`drive_indexer.py`**:
  - SQLite persistent cache for indexed files and folders.
  - Background crawler with batch upserting, file type filtering, and subfolder traversal.

### 6. Time Intelligence Layer (`src/tools/`)

**Responsibility**: Timezone-aware date calculations and deterministic relative time resolution.

- **`time_tool.py`**:
  - Configurable timezone support (`Africa/Cairo`).
  - Deterministic mathematical resolution of relative time expressions ("tomorrow at 3pm", "كمان ساعتين", "Sunday next week", "بعد بكرة") that patches LLM tool outputs to prevent date/time hallucination.

### 7. WhatsApp Layer (`src/whatsapp/`)

**Responsibility**: WhatsApp gateway communication, media extraction, schedule ingestion, and calendar synchronization.

- **`evolution_client.py`**:
  - Full async REST client for Evolution API v2.
  - Media decryption via `/chat/getBase64FromMediaMessage`.
  - Session lifecycle management (connect, QR, reset, status).
- **`bot.py`**:
  - Webhook dispatcher with **synchronized real-time typing presence** (`composing` triggers immediately upon message arrival).
  - Delivers replies instantly upon model completion with **zero artificial sleep delays**.
  - Routes group messages and direct messages to the tool-calling agent.
- **`group_listener.py`**:
  - Multimodal schedule parser utilizing `llm_router`.
  - Extracts structured JSON schedule events from Arabic/English text and university timetable images.
- **`calendar_sync.py`**:
  - Integration with Google Calendar API using non-blocking `asyncio.to_thread` execution.
  - `cache_discovery=False` to eliminate legacy oauth2client file cache warnings.
  - Syncs to a shared secondary calendar for multi-user access.
  - Automatically records the original student message or announcement verbatim in the event's description.
  - Color-coded events with automatic 1h & 15m reminders.
  - Intelligent deletion engine: supports bulk clearing (`delete all`), cross-language subject mapping, and LLM matching.

### 8. API Layer (`src/api/`)

**Responsibility**: FastAPI HTTP interface, lifecycle management, and webhooks.

```
POST /webhook          ← WhatsApp incoming messages (from Evolution API)
POST /query            ← Direct Q&A endpoint (routes through llm_router)
GET  /schedule         ← Upcoming events from Google Calendar
POST /schedule/delete  ← Delete event endpoint (keyword + date)
GET  /groups           ← List joined WhatsApp groups and JIDs
GET  /drive/search     ← Search indexed college drive materials
POST /drive/sync       ← Trigger a re-indexing crawl of Google Drive
GET  /drive/stats      ← Return Drive indexing statistics
GET  /time             ← Bot's current timezone-aware date and time info
GET  /qr               ← Browser-based interactive pairing dashboard
GET  /qr/json          ← Real-time QR base64 and connection state
POST /qr/reset         ← Re-initialize session and generate fresh QR
GET  /health           ← System health check (reports whatsapp_connected)
GET  /docs             ← Auto-generated OpenAPI Swagger documentation
```

## Deployment Topology (Oracle Cloud)

```
Oracle Cloud Always Free VM
├── 4 ARM vCPUs, 24 GB RAM, 200 GB Disk
│
├── Docker Compose
│   ├── python-backend (FastAPI + LangGraph + ChromaDB)
│   │   ├── Port 8000 (API)
│   │   └── ./chroma_db/ (persistent volume)
│   │
│   ├── evolution-api (WhatsApp Gateway)
│   │   ├── Port 8080 (REST API)
│   │   └── Webhooks → python-backend:8000/webhook
│   │
│   └── (optional) redis
│       └── Port 6379 (task queue)
│
├── Systemd Services
│   └── docker-compose auto-restart on boot
│
└── Nginx Reverse Proxy
    └── Port 443 (HTTPS) → internal services
```

## Security Considerations

- All API keys stored in `.env` (never committed)
- WhatsApp session auth stored in Docker volume
- Rate limiting on API endpoints
- Input sanitization before LLM prompts (prevent injection)
- HTTPS via Let's Encrypt on the Oracle VM
