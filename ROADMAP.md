# 🗺️ Mnemo — Roadmap

> Long-term goal: transform Mnemo into an intelligent,  
> modular and extensible desktop assistant — capable of powering interfaces as diverse  
> as a terminal, a Raspberry Pi robot, or a Unity desktop pet.

---

## ✅ Phase 0 — Memory Foundations *(completed)*

The core of the project. Everything else builds on top of it.

- [x] Hybrid memory architecture (short-term JSON + long-term Markdown)
- [x] Dual SQLite index: FTS5 keyword + vector (nomic-embed-text, 768d)
- [x] Hybrid retrieval using Reciprocal Rank Fusion (RRF)
- [x] Chunk weighting: importance by category × freshness (exponential half-life decay)
- [x] `memory.md` ↔ SQLite desynchronization detection (MD5 hash + mtime)
- [x] ConversationCrew (Evaluator → MemoryRetriever → Main Agent)
- [x] ConsolidationCrew (SessionConsolidator → MemoryWriter)
- [x] CTRL+C protection via `finally` + orphaned session recovery
- [x] Unicode surrogate sanitization (Ollama bug)
- [x] Separate YAML per crew (CrewAI KeyError fix)
- [x] Ollama Modelfile with `num_ctx 8192` (`num_ctx 16 384`)

---

## ✅ Phase 1 — Stabilization *(completed)*

Make the system reliable over time before adding new features.

- [x] **Level 1 unit tests** — low-level building blocks without LLM
  - [x] `parse_markdown_chunks` — correct splitting of `##` / `###`
  - [x] `compute_hash` — determinism and change sensitivity
  - [x] `update_markdown_section` — upsert without duplication, neighboring sections intact
  - [x] `sync_markdown_to_db` — chunk addition, update, deletion
  - [x] `load_session_json` — handle empty, corrupted, or missing files
  - [x] `freshness_score` / `importance_score` — correct values and decay behavior
- [x] **Level 2 tests** — hybrid retrieval (with Ollama, without reasoning LLM)
  - [x] Manual chunk insertion → verify top-1 retrieval result
  - [x] Short query → adaptive_weights switches to keyword mode
  - [x] Empty query → no crash
- [x] **Level 3 tests** — full session cycle (without LLM)
  - [x] `update_session_memory` × N → correct accumulation
  - [x] Empty session scenario → "nothing to consolidate" without crash
- [x] Add a `CONTRIBUTING.md` and GitHub issue templates
- [?] Support for CrewAI `knowledge/` as an optional documentation layer  
  *(static, factual — distinct from episodic `memory.md` memory)*

---

## 🔧 Phase 2 — Perception *(completed)*

Give Mnemo the ability to perceive its environment beyond typed text.

- [x] **File ingestion**
  - [x] PDF → text extraction + chunking → long-term memory injection
  - [x] DOCX, TXT, Markdown
  - [x] Source code (with language detection)
- [x] **Temporal awareness**
  - [x] Automatic injection of current date/time into each session
  - [x] Connection to a local ICS calendar (read-only at first)
  - [x] Awareness of upcoming deadlines and events
- [x] **Occasional web access** *(security verified)*
  - [x] Self-hosted SearXNG integration via Docker (zero tracking)
  - [x] DuckDuckGo API fallback if SearXNG is unavailable
  - [x] `web_search` tool available only upon explicit request
  - [x] Security audit of network dependencies before activation

---

## ⚡ Phase 3 — Action & Local Interface *(completed)*

Move from an agent that responds to an agent that acts — and give it a window onto the desktop.

- [x] **Action tools**
  - [x] Shell command execution (mandatory confirmation, never autonomous)
  - [x] File management (create, move, rename)
  - [x] Structured note-taking → direct writing into `memory.md` or project files
- [x] **Local web dashboard**
  - [x] Lightweight `localhost` interface (FastAPI + minimal frontend)
  - [x] Visualization of `memory.md` and sessions
  - [x] Agenda with CRUD backend
  - [x] Send messages from the browser (CLI alternative)
  - [x] *Why web over system tray: better WSL2 portability,  
    naturally prepares the Phase 4 API*
- [x] **Scheduler**
  - [x] Scheduled tasks (reminders, daily summary)
  - [x] Morning briefing: today’s agenda + last session + key memory highlights

---

## 🌐 Phase 4 — API & External Interfaces *(In progress)*

Turn Mnemo into a headless brain callable from any interface.

- [x] **REST API (FastAPI)**
  - [x] `POST /message` — send a message, receive a response
  - [x] `GET /memory` — read long-term memory
  - [x] `POST /memory` — write a fact directly into memory
  - [x] `GET /session/{id}` — session history
  - [ ] Lightweight authentication (local token, no public exposure)
  - [ ] WebSocket for token-by-token response streaming
- [ ] **Local TTS / STT**
  - [ ] Speech-to-Text via Whisper.cpp (offline, WSL compatible)
  - [ ] Text-to-Speech via Piper TTS (lightweight local voice)
  - [ ] Voice → Mnemo → Voice pipeline
- [ ] **Raspberry Pi integration**
  - [ ] Lightweight Python client consuming the REST API
  - [ ] Latency optimization for embedded hardware responses
  - [ ] Fallback mode if Mnemo is unreachable (local cache responses)
- [ ] **Unity integration (desktop pet)**
  - [ ] C# client consuming the REST API
  - [ ] State protocol: mood, attention, reaction to messages
  - [ ] Token streaming to animate the character in real time
  - [ ] Events: `on_thinking`, `on_response`, `on_memory_write`

---

## 🚀 Phase 5 — Proactivity *(advanced vision)*

The agent takes initiative without waiting to be prompted.

- [ ] Contextual suggestions based on time and memory
- [ ] Pattern detection ("you work on X every Monday")
- [ ] Alerts for approaching deadlines
- [ ] Automatic compaction of `memory.md` when it becomes too large
- [ ] Multi-profiles (separate identities for personal vs professional use)
- [ ] **GOAP planner for scheduler actions**
  - [ ] Replace the current flat `_ACTION_MAP` dispatch with a Goal-Oriented Action Planning layer
  - [ ] Actions declared with preconditions and effects (e.g. `deadline_alert` requires `briefing_fresh`)
  - [ ] LLM expresses a *goal state*, the planner finds the optimal action sequence
  - [ ] Enables conflict detection, dependency ordering, and extensibility without prompt changes
  - [ ] Migration path: preconditions/effects already expressible as metadata on `_ACTION_MAP` entries

---

## 🔐 Cross-cutting Principles

These constraints apply to all phases:

- **Privacy first** — no personal data leaves the machine without explicit consent
- **Offline first** — every feature must work without a connection; the web is a bonus, never a dependency
- **Confirmation before action** — any irreversible action (file, shell, sending) requires validation
- **Auditability** — `memory.md` remains human-readable and editable at all times
- **Web tool security** — every network tool is audited before integration

---

## 📌 Legend

| Symbol | Meaning |
|---|---|
| ✅ | Completed |
| 🔧 | In progress |
| 👁️ | Planned — short term |
| ⚡ | Planned — mid term |
| 🌐 | Vision — long term |
| 🚀 | Advanced vision |