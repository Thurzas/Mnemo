"""
Dispatch — registre de crews + logique de pre-fetch contexte.

Séparé du routing : ne connaît pas les handlers, uniquement le RouterResult.
Ajouter un crew = une ligne dans CREW_REGISTRY.

GOAP-ready : CREW_REGISTRY est l'embryon d'ActionLibrary.
  Pour la transition GOAP, ajouter preconditions/effects comme métadonnées
  par entrée — la structure du registre n'a pas besoin de changer.
"""

from __future__ import annotations

import json
from datetime import date as _date

from .context import RouterResult


# ── Registre des crews ────────────────────────────────────────────────────────
# Chaque entrée : route_name → crew_class (importée à la demande pour éviter
# les imports circulaires au niveau module).
#
# Convention : la clé est la valeur de RouterResult.route.
# Route inconnue → ConversationCrew (fallback silencieux).

def _get_crew_registry() -> dict:
    """Retourne le registre — imports différés pour éviter les circulaires."""
    from Mnemo.crew import (
        ConversationCrew, ShellCrew, CalendarWriteCrew,
        SchedulerCrew, NoteWriterCrew, BriefingCrew,
    )
    return {
        "conversation": ConversationCrew,
        "shell":        ShellCrew,
        "calendar":     CalendarWriteCrew,
        "scheduler":    SchedulerCrew,
        "note":         NoteWriterCrew,
        "briefing":     BriefingCrew,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prefetch_calendar(metadata: dict) -> str:
    """
    Pré-fetch calendrier si needs_calendar=True.

    Retourne un bloc texte complet : deadlines urgentes + agenda.
    Injecté dans calendar_context — le seul endroit où le LLM voit les événements.
    (temporal_context ne contient plus l'agenda depuis la séparation date/calendrier.)
    """
    if not metadata.get("needs_calendar"):
        return ""
    try:
        from Mnemo.tools.calendar_tools import (
            get_upcoming_events, get_events_for_date, format_events_for_prompt,
            get_deadline_context,
        )
        ref_date_str = metadata.get("reference_date")
        if ref_date_str:
            ref_date   = _date.fromisoformat(ref_date_str)
            cal_events = get_events_for_date(ref_date)
        else:
            cal_events = get_upcoming_events(days=21)

        parts = []
        deadline_block = get_deadline_context()
        if deadline_block:
            parts.append(deadline_block)
        if cal_events:
            parts.append(format_events_for_prompt(cal_events))
        return "\n\n".join(parts) if parts else "Aucun événement trouvé."
    except Exception as e:
        return f"Erreur calendrier : {e}"


# ── Dispatch ──────────────────────────────────────────────────────────────────

def dispatch(
    result: RouterResult,
    user_message: str,
    session_id: str,
    temporal_ctx: str,
    web_context: str,
    shell_command: str = "",
) -> str:
    """
    Dispatche vers le bon crew selon result.route.

    Routes reconnues : conversation, shell, calendar, scheduler, note, briefing.
    Route inconnue → ConversationCrew (fallback silencieux).
    """
    registry        = _get_crew_registry()
    route           = result.route
    metadata        = result.metadata
    eval_raw        = json.dumps(metadata, ensure_ascii=False)
    calendar_context = _prefetch_calendar(metadata)

    base_inputs = {
        "user_message":      user_message,
        "evaluation_result": eval_raw,
        "temporal_context":  temporal_ctx,
        "web_context":       web_context,
        "calendar_context":  calendar_context,
        "_web_mode":         metadata.get("_web_mode", False),
    }

    if route == "shell":
        from Mnemo.crew import ShellCrew
        return ShellCrew().run({**base_inputs, "shell_command": shell_command})

    if route == "note":
        from Mnemo.crew import NoteWriterCrew
        return NoteWriterCrew().run({"user_message": user_message})

    # Tous les autres crews (calendar, scheduler, briefing) — interface uniforme .run()
    crew_cls = registry.get(route)
    if crew_cls and route != "conversation":
        return crew_cls().run({**base_inputs})

    # Conversation (défaut) — interface .crew().kickoff()
    from Mnemo.crew import ConversationCrew
    conv_result = ConversationCrew().crew().kickoff(inputs={
        **base_inputs,
        "session_id":     session_id,
        "memory_context": "",
    })
    return conv_result.raw


def build_router():
    """Construit et retourne la chaîne de handlers complète."""
    from .handlers.keyword import KeywordHandler
    from .handlers.ml      import MLHandler
    from .handlers.llm     import LLMHandler

    keyword = KeywordHandler()
    ml      = MLHandler()
    llm     = LLMHandler()
    keyword.set_next(ml).set_next(llm)
    return keyword