# 🐠•°•°•°•°°~ Mnemo Crew °:•.🐠*.•🪸.•:°

Welcome to the Mnemo Crew project, powered by [crewAI](https://crewai.com). This template is designed to help you set up a multi-agent AI system with ease, leveraging the powerful and flexible framework provided by crewAI. Our goal is to enable your agents to collaborate effectively on complex tasks, maximizing their collective intelligence and capabilities.

## What is Mnemo ?

It's an assembly of crews, each with its own objective: the conversational crew can search two types of memory: short-term, session-based, and long-term, based on a Markdown file with a database for indexing. I'm using FTS5 as a search engine for vectors and key word.

The consolidation crew is used to synchronize and maintain the database and the Markdown file.

## Installation

Ensure you have Python >=3.10 <3.13 installed on your system. This project uses [UV](https://docs.astral.sh/uv/) for dependency management and package handling, offering a seamless setup and execution experience.

First, if you haven't already, install uv:

```bash
pip install uv
```

Next, navigate to your project directory and install the dependencies:

(Optional) Lock the dependencies and install them by using the CLI command:
```bash
crewai install
```
### Customizing

**Add your `OPENAI_API_KEY` into the `.env` file**

- Modify `src/Mnemo/config/agents.yaml` to define your agents
- Modify `src/Mnemo/config/tasks.yaml` to define your tasks
- Modify `src/Mnemo/crew.py` to add your own logic, tools and specific args
- Modify `src/Mnemo/main.py` to add custom inputs for your agents and tasks

## Running the Project

To kickstart your crew of AI agents and begin task execution, run this from the root folder of your project:

```bash
$ crewai run
```

This command initializes the Mnemo Crew, assembling the agents and assigning them tasks as defined in your configuration.

This example, unmodified, will run the create a `report.md` file with the output of a research on LLMs in the root folder.

## Understanding Your Crew

The Mnemo Crew is composed of multiple AI agents, each with unique roles, goals, and tools. These agents collaborate on a series of tasks, defined in `config/tasks.yaml`, leveraging their collective skills to achieve complex objectives. The `config/agents.yaml` file outlines the capabilities and configurations of each agent in your crew.


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
- [x] Ollama Modelfile with `num_ctx 8192`

---

## 🔧 Phase 1 — Stabilization *(in progress)*

Make the system reliable over time before adding new features.

- [ ] **Level 1 unit tests** — low-level building blocks without LLM
  - [ ] `parse_markdown_chunks` — correct splitting of `##` / `###`
  - [ ] `compute_hash` — determinism and change sensitivity
  - [ ] `update_markdown_section` — upsert without duplication, neighboring sections intact
  - [ ] `sync_markdown_to_db` — chunk addition, update, deletion
  - [ ] `load_session_json` — handle empty, corrupted, or missing files
  - [ ] `freshness_score` / `importance_score` — correct values and decay behavior
- [ ] **Level 2 tests** — hybrid retrieval (with Ollama, without reasoning LLM)
  - [ ] Manual chunk insertion → verify top-1 retrieval result
  - [ ] Short query → adaptive_weights switches to keyword mode
  - [ ] Empty query → no crash
- [ ] **Level 3 tests** — full session cycle (without LLM)
  - [ ] `update_session_memory` × N → correct accumulation
  - [ ] Empty session scenario → "nothing to consolidate" without crash
- [ ] Add a `CONTRIBUTING.md` and GitHub issue templates
- [ ] Support for CrewAI `knowledge/` as an optional documentation layer  
  *(static, factual — distinct from episodic `memory.md` memory)*

---

## 👁️ Phase 2 — Perception *(upcoming)*

Give Mnemo the ability to perceive its environment beyond typed text.

- [ ] **File ingestion**
  - [ ] PDF → text extraction + chunking → long-term memory injection
  - [ ] DOCX, TXT, Markdown
  - [ ] Source code (with language detection)
- [ ] **Temporal awareness**
  - [ ] Automatic injection of current date/time into each session
  - [ ] Connection to a local ICS calendar (read-only at first)
  - [ ] Awareness of upcoming deadlines and events
- [ ] **Occasional web access** *(security verified)*
  - [ ] Self-hosted SearXNG integration via Docker (zero tracking)
  - [ ] DuckDuckGo API fallback if SearXNG is unavailable
  - [ ] `web_search` tool available only upon explicit request
  - [ ] Security audit of network dependencies before activation

---

## ⚡ Phase 3 — Action & Local Interface *(upcoming)*

Move from an agent that responds to an agent that acts — and give it a window onto the desktop.

- [ ] **Action tools**
  - [ ] Shell command execution (mandatory confirmation, never autonomous)
  - [ ] File management (create, move, rename)
  - [ ] Structured note-taking → direct writing into `memory.md` or project files
- [ ] **Local web dashboard**
  - [ ] Lightweight `localhost` interface (FastAPI + minimal frontend)
  - [ ] Visualization of `memory.md` and sessions
  - [ ] Send messages from the browser (CLI alternative)
  - [ ] *Why web over system tray: better WSL2 portability,  
    naturally prepares the Phase 4 API*
- [ ] **Scheduler**
  - [ ] Scheduled tasks (reminders, daily summary)
  - [ ] Morning briefing: today’s agenda + last session + key memory highlights

---

## 🌐 Phase 4 — API & External Interfaces *(vision)*

Turn Mnemo into a headless brain callable from any interface.

- [ ] **REST API (FastAPI)**
  - [ ] `POST /message` — send a message, receive a response
  - [ ] `GET /memory` — read long-term memory
  - [ ] `POST /memory` — write a fact directly into memory
  - [ ] `GET /session/{id}` — session history
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
## Support

For support, questions, or feedback regarding the Mnemo Crew or crewAI.
- Visit our [documentation](https://docs.crewai.com)
- Reach out to us through our [GitHub repository](https://github.com/joaomdmoura/crewai)
- [Join our Discord](https://discord.com/invite/X4JWnZnxPb)
- [Chat with our docs](https://chatg.pt/DWjSBZn)

Let's create wonders together with the power and simplicity of crewAI.
