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

**Responsibility**: Resilient multi-provider routing and agentic verification.

- **`llm_router.py`**:
  - Automatically rotates requests across Google Gemini (`gemini-2.5-flash-lite`, `gemini-flash-latest`, `gemini-3.5-flash-lite`) and Groq Cloud (`llama-3.3-70b-versatile`, `qwen/qwen3.8-27b`, `allam-2-7b`).
  - Shields the bot from Google free-tier 429 quota exhaustion.
  - Supports multimodal vision inputs (`PIL.Image`).

**LangGraph State**:
```python
class AgentState(TypedDict):
    query: str                    # Original user question
    query_type: str               # factual | conceptual | exam-prep | schedule
    retrieved_chunks: list        # Top-k relevant chunks
    retrieved_images: list        # Associated images/frames
    llm_responses: dict           # {provider: response} from each LLM
    consensus: bool               # Do the LLMs agree?
    confidence: float             # 0.0 - 1.0
    final_answer: str             # Merged answer
    sources: list                 # Citations
    user_confirmed: bool          # Has the user confirmed?
    export_path: str | None       # Path to generated image/PDF
```

### 5. WhatsApp Layer (`src/whatsapp/`)

**Responsibility**: WhatsApp gateway communication, media extraction, schedule ingestion, and calendar synchronization.

- **`evolution_client.py`**:
  - Full async REST client for Evolution API v2.
  - Media decryption via `/chat/getBase64FromMediaMessage`.
  - Session lifecycle management (connect, QR, reset, status).
- **`bot.py`**:
  - Webhook dispatcher with humanized typing presence (`composing`, jittered delay between 1.5s - 4.0s).
  - Routes group messages to `GroupListener` and direct messages to study assistant.
- **`group_listener.py`**:
  - Multimodal schedule parser utilizing `llm_router`.
  - Extracts structured JSON schedule events from Arabic/English text and university timetable images.
- **`calendar_sync.py`**:
  - Direct integration with Google Calendar API.
  - Syncs to a shared secondary calendar (`Option 1`) for multi-user access.
  - Color-coded events with automatic 1h & 15m reminders.
  - Intelligent deletion engine: supports bulk clearing (`delete all`), cross-language subject mapping, and LLM matching.

### 6. API Layer (`src/api/`)

**Responsibility**: FastAPI HTTP interface, lifecycle management, and webhooks.

```
POST /webhook          ← WhatsApp incoming messages (from Evolution API)
POST /query            ← Direct Q&A endpoint (routes through llm_router)
GET  /schedule         ← Upcoming events from Google Calendar
POST /schedule/delete  ← Delete event endpoint (keyword + date)
GET  /groups           ← List joined WhatsApp groups and JIDs
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
