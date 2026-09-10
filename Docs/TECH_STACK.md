# 🛠️ Tech Stack — Detailed Breakdown

Every technology in this project was selected for **zero cost** operation at meaningful scale.

---

## 1. Multi-LLM Router Architecture (`src/agents/llm_router.py`)

A centralized, unified multi-LLM gateway with model pooling, memory caching, and automatic failover across free-tier providers.

### Primary Multimodal & Tool Reasoning: Google Gemini
- **`gemini-3.5-flash` & `gemini-3.5-flash-lite`**:
  - High-speed reasoning, full multimodal vision (timetable image OCR), generous request budget.
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
  - Explicit **state machine** with nodes and edges (vs implicit chain)
  - Supports **cycles** (verification loop: ask → verify → re-ask)
  - Built-in **human-in-the-loop** (user confirmation before image generation)
  - Observable and debuggable execution graphs
  - First-class support for multi-agent architectures

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
- **Multi-User Sharing (Option 1)**: Supports writing to a dedicated shared secondary calendar ID (`GOOGLE_CALENDAR_ID` in `.env`), enabling unlimited students to view updates without requiring individual OAuth credentials.
- **Security**: `credentials.json` and `token.json` are excluded from Git via `.gitignore`.
- **Engine**:
  - Auto-creates color-coded events with reminders (1 hour and 15 mins prior).
  - Duplicate detection.
  - Intelligent deletion: bulk clearing, cross-language English ↔ Arabic translation, and LLM matching fallback.

### Google Drive API
- **Cost**: Free (15GB storage included with Google account)
- **Use**: Watch for new lecture uploads, auto-trigger ingestion

---

## 9. Additional Libraries

| Library | Purpose | Cost |
|:--------|:--------|:-----|
| **FastAPI** | Async Python web framework | $0 |
| **PyMuPDF (fitz)** | PDF text and image extraction | $0 |
| **Pillow** | Image processing and generation | $0 |
| **WeasyPrint** | HTML/CSS to PDF conversion | $0 |
| **LangChain** | RAG utilities, text splitters | $0 |
| **FFmpeg** | Video/audio processing | $0 |
| **pytest** | Testing framework | $0 |
| **Docker** | Containerization | $0 |
