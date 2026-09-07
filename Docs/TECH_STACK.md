# 🛠️ Tech Stack — Detailed Breakdown

Every technology in this project was selected for **zero cost** operation at meaningful scale.

---

## 1. LLM Providers (All Free Tier)

### Primary: Google Gemini 2.0 Flash
- **Free Limits**: 10-15 RPM, 1,000-1,500 RPD, 250k-1M TPM
- **Context Window**: 1,000,000+ tokens
- **Why chosen**: Massive context window (can fit entire lectures), native multimodal (text + images + audio), fastest free model
- **Use case**: Primary Q&A, vision-based problem solving (photo of a question), large document analysis

### Verification: Groq Cloud — Llama 3.3 70B
- **Free Limits**: 30 RPM, 1,000 RPD, 12,000 TPM (no credit card required)
- **Context Window**: 128,000 tokens
- **Why chosen**: 300-800 tokens/second generation speed (custom LPU hardware), GPT-4-class quality
- **Use case**: Independent answer verification, fast reasoning

### Backup: Cerebras Cloud — Llama 3.3 70B
- **Free Limits**: 30 RPM, 60,000 TPM, 1,000,000 tokens/day
- **Context Window**: 128,000 tokens (8k burst on free tier)
- **Why chosen**: Fastest inference in the world (1,800+ tok/s on 8B model), generous daily token budget
- **Use case**: Tertiary verification, backup when Groq limits hit

### Fallback Chain: OpenRouter Free → GitHub Models → Ollama
- **OpenRouter**: 20 RPM, ~200 RPD — access to DeepSeek R1, Llama 3.3, Gemini Flash for free
- **GitHub Models**: 15 RPM, 50-150 RPD — access to GPT-4o, Claude 3.5 Sonnet via PAT
- **Ollama (Local)**: Unlimited — runs locally when all cloud limits are exhausted

### Combined Daily Capacity (Free)
| Provider | Daily Requests | Role |
|:---------|:--------------|:-----|
| Gemini | ~1,500 | Primary answers |
| Groq | ~1,000 | Verification |
| Cerebras | ~500-1,000 | Verification |
| OpenRouter | ~200 | Fallback |
| GitHub Models | ~50-150 | Emergency fallback |
| Ollama | Unlimited | Offline/batch |
| **Total** | **~3,500-4,000+** | |

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

### Option A: Baileys (Direct)
- **Type**: TypeScript/Node.js library, direct WebSocket to WhatsApp
- **RAM**: ~80MB (no Chromium)
- **Pros**: Lightweight, fast, well-maintained
- **Cons**: Need to write Node.js bridge to Python backend

### Option B: Evolution API (Recommended)
- **Type**: Self-hosted Docker container wrapping Baileys
- **RAM**: ~150MB
- **Pros**: Exposes a full REST API + webhooks — Python can call it directly
- **Cons**: Extra Docker container

### Current: whatsapp-web.js
- **RAM**: 300MB-1GB (runs headless Chromium)
- **Status**: Works but heavy; will be migrated

---

## 7. Hosting: Oracle Cloud Always Free

- **Compute**: 4 Ampere ARM vCPUs + 24GB RAM
- **Storage**: 200GB block storage
- **Network**: 10TB/month egress, static IPv4
- **Uptime**: 24/7 always on (never sleeps, never expires)
- **Cost**: $0 forever (requires credit card for verification only)
- **Why chosen**: Enough resources to run the entire stack (Python backend + WhatsApp gateway + ChromaDB + Whisper) on a single VM

---

## 8. APIs & Integrations

### Google Drive API
- **Cost**: Free (15GB storage included with Google account)
- **Use**: Watch for new lecture uploads, auto-trigger ingestion

### Google Calendar API
- **Cost**: Free
- **Use**: Sync parsed schedule events from WhatsApp announcement group

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
