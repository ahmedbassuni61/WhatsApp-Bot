# 🎓 College Assistant AI — Project Overview

## Vision

A **free, AI-powered college assistant** that turns raw lecture materials (PDFs, videos, audio) into an intelligent knowledge base accessible via WhatsApp. Students ask questions in natural language and receive verified, cited answers — with the option to export them as study-ready images or PDFs.

## Problem Statement

College students deal with fragmented study resources:
- Lecture slides scattered across drives
- Recorded lectures that are hours long with no searchable index
- Previous exam papers with no organized Q&A
- Schedule announcements buried in WhatsApp group chats

**This project solves all of these** by creating a unified AI assistant that understands all your course materials and is available 24/7 on WhatsApp.

## Key Features

### 📚 Multi-Source RAG (Retrieval-Augmented Generation)
- **PDF Ingestion**: Lecture slides, textbook chapters, and previous exams are chunked, embedded, and stored in a vector database
- **Video Processing**: Lecture videos are transcribed (Whisper), key frames extracted, and indexed by timestamp
- **Audio Processing**: Standalone audio recordings are transcribed and searchable
- **Smart Retrieval**: Questions search across ALL sources simultaneously with source citations

### 🤖 Agentic Multi-LLM Verification (LangGraph)
- Questions are answered by **multiple LLMs independently** (Gemini, Groq/Llama, Cerebras)
- Answers are compared for **consensus verification** — disagreements are flagged
- A confidence score is attached to every answer
- The entire reasoning cycle is a **LangGraph state machine** with observable steps

### 📱 WhatsApp Integration
- Ask questions directly via WhatsApp (text or image of a problem)
- Receive answers with source citations (which lecture, which page/timestamp)
- Relevant lecture slides or video frames are attached as images

### 📅 Schedule & Calendar Sync
- Monitors announcement groups or direct messages for exam timetables, section announcements, and lecture updates.
- **Multimodal Timetable Image Parsing**: Uses Gemini Flash Lite vision to read complex, multi-column university exam schedules in Arabic and English (e.g. Faculty of Engineering schedules) and automatically schedules exams with times, locations, and descriptions in seconds.
- **Multi-User Shared Calendar (Option 1)**: Syncs to a dedicated shared Google Calendar, allowing students and faculty to subscribe and receive real-time updates and reminders.
- **Natural Language & Bulk Event Deletion**: Delete events on-demand (`/delete math`, `delete all exam schedule`, `احذف امتحان الرياضيات`).
- Query upcoming schedule via `/schedule` or `جدول`.

### 🖼️ Answer-to-Image Export
- When a student **confirms** an answer is correct, it's converted into a styled study image or PDF
- Clean typography, proper formatting, source citations included
- Shareable in study groups

## Architecture Highlights

| Concept | Implementation |
|:--------|:---------------|
| **Agentic AI** | LangGraph state machine with query routing, retrieval, multi-LLM verification, and answer composition |
| **Multi-LLM Router** | Automatic failover across Google Gemini (Flash Lite / Latest) and Groq Cloud (Llama 3.3 70B, Qwen, Allam) |
| **RAG** | ChromaDB vector store + FastEmbed embeddings + semantic chunking |
| **Multi-Modal** | Gemini vision for timetable and problem analysis, Whisper for audio/video transcription |
| **Cost** | **$0/month** — all free-tier APIs, local embeddings, open-source tools |

## Tech Stack

- **Python** (FastAPI, LangGraph, LangChain, Pydantic)
- **Evolution API v2 & PostgreSQL** (WhatsApp Gateway via Baileys)
- **ChromaDB** (vector database)
- **Google Gemini (Flash Lite / Latest)** + **Groq Cloud (Llama 3.3 70B, Qwen, Allam)** (LLMs)
- **Google Calendar API** (shared calendar sync with automated reminders)
- **FastEmbed** (local embeddings)
- **OpenAI Whisper** (transcription)
- **Docker & Docker Compose** (full stack containerization)
- **Oracle Cloud Always Free** (hosting)

## Skills Demonstrated (for CV)

1. **Agentic AI & LangGraph** — Multi-agent state machines with decision routing and verification loops
2. **RAG Systems** — End-to-end retrieval pipeline: ingestion → embedding → vector search → context assembly
3. **Multi-LLM Orchestration** — Provider routing, rate limit management, consensus verification
4. **NLP/ML Engineering** — Whisper transcription, semantic chunking, embedding models
5. **Systems Design** — Microservices, Docker, REST APIs, event-driven architecture
6. **Full-Stack Integration** — WhatsApp bot, Google Calendar, Google Drive, image generation
7. **Cost Engineering** — Enterprise-grade system on $0 infrastructure
