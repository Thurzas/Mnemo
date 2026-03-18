"""
RouterContext et RouterResult — structures de données partagées par toute la chaîne.

RouterContext est conçu pour être extensible vers un WorldState GOAP :
  - les champs optionnels (recent_messages, user_profile, memory_state, available_tools)
    sont ignorés aujourd'hui mais seront activés quand le planner en aura besoin.
  - _hints est un canal inter-handlers : MLHandler y dépose ml_route/ml_conf
    pour que LLMHandler puisse faire l'arbitrage sans couplage direct.
"""

from dataclasses import dataclass, field


@dataclass
class RouterContext:
    # ── Requis ──────────────────────────────────────────────────────────
    message:          str
    session_id:       str
    temporal_context: str = ""

    # ── GOAP-ready (optionnels, ignorés aujourd'hui) ─────────────────────
    recent_messages:  list[dict] = field(default_factory=list)
    user_profile:     dict       = field(default_factory=dict)
    memory_state:     dict       = field(default_factory=dict)
    available_tools:  list[str]  = field(default_factory=list)

    # ── Canal inter-handlers (interne, ne pas exposer à l'extérieur) ─────
    # Utilisé par MLHandler pour transmettre ml_route/ml_conf à LLMHandler.
    _hints: dict = field(default_factory=dict)


@dataclass
class RouterResult:
    route:      str
    confidence: float
    handler:    str           # "keyword" | "ml" | "llm" | "fallback"
    metadata:   dict = field(default_factory=dict)
    # metadata transporte tout ce dont dispatch() et confirmation.py ont besoin :
    #   needs_web, web_query, needs_calendar, reference_date,
    #   needs_clarification, clarification_reason,
    #   shell_command, _web_mode, ml_conf
