# 🗺️ Mnemo — Roadmap

> Long-term goal: transform Mnemo into an intelligent, modular and extensible personal assistant —
> capable of powering interfaces as diverse as a terminal, a React dashboard, a Raspberry Pi robot,
> or a Unity desktop pet.

---

## ✅ Phase 0 — Memory Foundations *(completed)*

- [x] Hybrid memory architecture (short-term JSON + long-term Markdown)
- [x] Dual SQLite index: FTS5 keyword + vector (nomic-embed-text, 768d)
- [x] Hybrid retrieval using Reciprocal Rank Fusion (RRF)
- [x] Chunk weighting: category × freshness (exponential half-life decay)
- [x] `memory.md` ↔ SQLite desynchronization detection (MD5 + mtime)
- [x] ConversationCrew (Evaluator → MemoryRetriever → Main Agent)
- [x] ConsolidationCrew (SessionConsolidator → MemoryWriter)
- [x] Orphaned session recovery on startup

---

## ✅ Phase 1 — Stabilization *(completed)*

- [x] Level 1–3 unit tests (memory, retrieval, session cycle)
- [x] Active learning: weight regression + uncertain case audit trail
- [x] ML router (sklearn pipeline, confidence thresholds per route)
- [x] `CONTRIBUTING.md` + GitHub issue templates

---

## ✅ Phase 2 — Perception *(completed)*

- [x] PDF / DOCX / TXT / Markdown / source code ingestion
- [x] Temporal context: current date/time injected into every session
- [x] ICS calendar integration (read + CRUD)
- [x] Web search: SearXNG self-hosted + DuckDuckGo fallback (PII guards, audit log)

---

## ✅ Phase 3 — Action & Local Interface *(completed)*

- [x] Shell execution (whitelist + mandatory confirmation)
- [x] Structured note-taking → direct write to `memory.md`
- [x] FastAPI dashboard: memory, sessions, calendar, chat
- [x] Scheduler: daily briefing, weekly summary, deadline scan

---

## ✅ Phase 4 — API & Voice *(completed)*

- [x] REST API: `/message`, `/memory`, `/sessions`, `/calendar`, `/confirmations`
- [x] WebSocket token streaming
- [x] JWT-style local auth (token per user, SHA-256 hash)
- [x] STT (Whisper.cpp offline) + TTS (Kokoro + RVC pipeline)
- [x] VoicePage + settings in dashboard

---

## ✅ Phase 5 — Proactivity & Routing *(completed)*

- [x] Hybrid CoR routing: KeywordHandler → MLHandler → LLMHandler
- [x] CuriosityCrew: memory gap detection + questions to user
- [x] WorldState + GOAP planner (backward chaining, A* ready)
- [x] PlannerCrew + ReconnaissanceCrew (HTN, max depth 2)
- [x] PlanRunner: `_run_shell`, `_run_note`, `_run_recon`, `_try_reformulate`
- [x] Stack detection: `_detect_stack()` + `_read_src_files()` for context-aware code gen
- [x] Phase E: project index (E.1) + doc RAG context (E.2)
- [x] Phase B: KG feedback ±0.1 per step, shell writes to `src/`

---

## ✅ Phase 6 — Dashboard & Projects *(completed)*

- [x] React dashboard: ChatPage, MemoryPage, SessionsPage, CalendarPage
- [x] KnowledgePage (doc ingestion + list)
- [x] ProjectsPage: Monaco editor + FileTree + Plan panel + Terminal
- [x] VoicePage: Kokoro voice settings + RVC model management
- [x] `pending_confirmations` → approval flow in ProjectsPage
- [x] Sandbox tools: `create/read/write/run_command`, git integration, path confinement

---

## ✅ Phase A — Assistant Identity *(completed — 2026-03-28)*

Configurable assistant persona, injected into all crews at runtime.

- [x] `assistant.json` per user (`data/users/<username>/assistant.json`)
- [x] `tools/assistant_tools.py`: `get/ensure/set_assistant_config`, `get_assistant_context`
- [x] Injection pipeline: `dispatch.py` → `base_inputs` → all crew YAML agents
- [x] `config/conversation_agents.yaml`: `{assistant_name}` / `{assistant_persona}`
- [x] `config/consolidation_agents.yaml` + `curiosity_agents.yaml`: persona voice
- [x] `GET /api/assistant` + `PUT /api/assistant`
- [x] `SettingsPage.tsx`: form with name / pronouns / persona_short / persona_full / language_style

---

## ✅ Phase D — DreamerCrew *(completed — 2026-03-30)*

Inactivity-triggered memory consolidation — the assistant "dreams" while the user is away.

- [x] **D0** Inactivity detector: `_should_dream()`, `_dream_tick()` in scheduler
  - Triggers after 30 min idle + 24h since last dream
- [x] **D1** `dreamer_tools.py`: `scan_sessions()`, `resolve_dates()` (FR relative → ISO)
- [x] **D2** `dreamer_tools.py`: `detect_exact_duplicates()`, `detect_dead_references()`, `build_dedup_report()`
- [x] **D3** `DreamerCrew` (CrewAI): `memory_analyst` (patches JSON) → `memory_patcher` (ApplyDreamPatchesTool)
- [x] **D4** `memory_archive.py`: `archive_old_sessions()` (>90d), `archive_completed_projects()` (✅ >30d), `prune_memory()`
- [x] **D5** `_run_dreamer()`: DreamerCrew → prune_memory → sync DB (full pipeline)
- [x] **D6** `POST /api/dream` + `GET /api/dream/log` + DreamerSection in SettingsPage
- [x] **Context compression**: Option A (hot sections if issues) > Option B (section rotation via `last_dream_section_idx`)

---

## 🔧 Phase N — Nodal Interface & Augmented Autonomy

> Source: *Architecture de l'autonomie augmentée — refactorisation nodale et orchestration GOAP*
>
> Transform Mnemo from a crew-based pipeline into a visually configurable,
> plugin-extensible autonomous agent system.

### N1 — Visual Node Interface ✅

- [x] `GET /api/graph` — expose the crew/agent/tool graph as JSON
- [x] `NodalPage.tsx` — React Flow canvas, node types colour-coded, live status overlay, click → detail panel
- [x] `nodal` tab in NavBar + App.tsx

### N2 — GOAP Enrichment

Extend the planner with more actions and better planning structure.

- [ ] Add actions to `ACTION_REGISTRY`: `TriggerDream`, `ArchiveMemory`, `UpdateAssistantConfig`, `FetchWebContext`
- [ ] GOAP graph visualization in NodalPage (current world_state → active plan path)
- [ ] Expose `GET /api/goap/state` — current world_state + active plan JSON
- [ ] Dynamic goal injection: user can set a GOAP goal from the nodal UI

### N3 — Guardrails & Risk Management ✅

- [x] **Risk taxonomy**: `RISK_REGISTRY` — low / medium / high / critical sur toutes les routes
- [x] **Audit log**: `data/users/<username>/audit_log.jsonl` — toutes les actions MEDIUM+ loggées
- [x] **Sidecar middleware** (FastAPI): bloque HIGH+ quand système en pause, log après réponse
- [x] **Kill-switch**: `POST /api/system/pause` / `resume` — suspend scheduler + bloque actions HIGH+
- [x] **Guardrails indicator** dans SettingsPage — état live, bouton pause/reprise, journal 20 dernières actions

### N4 — Plugin System v1

Allow users to define and register custom tools without modifying source code.

- [ ] JSON schema for tool definition: `{name, description, type, inputs, outputs, handler_path}`
- [ ] `tools/plugin_registry.py`: `register_tool()`, `load_plugins()`, `list_plugins()`
- [ ] `GET /api/plugins` + `POST /api/plugins` + `DELETE /api/plugins/{name}`
- [ ] Plugin node type in NodalPage: drag-and-drop to connect to a crew
- [ ] Plugins persisted in `data/users/<username>/plugins/`

---

## 👁️ Phase 7 — External Deployments *(planned)*

- [ ] Raspberry Pi lightweight client (REST API consumer)
- [ ] Unity desktop pet (C# client, streaming events: `on_thinking`, `on_response`, `on_memory_write`)
- [ ] Fine-tuning pipeline: LoRA adapter from accumulated session data (Unsloth + GGUF → Ollama)

---

## 🔐 Cross-cutting Principles

- **Privacy first** — no personal data leaves the machine without explicit consent
- **Offline first** — every feature must work without a connection
- **Confirmation before action** — any irreversible action requires explicit approval (HITL)
- **Auditability** — `memory.md` remains human-readable at all times; `audit_log.jsonl` for actions
- **Algorithm over LLM** — deterministic Python handles what doesn't need reasoning; LLM is last resort

---

## 📌 Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Phase completed |
| 🔧 | In progress this week |
| 👁️ | Planned — short term |
| ⚡ | Planned — mid term |
| 🌐 | Vision — long term |
