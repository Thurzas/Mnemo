"""
LLMHandler — EvaluationCrew comme fallback sémantique.

Toujours présent en fin de chaîne — produit toujours un RouterResult.
Applique l'arbitrage ML-vs-LLM : si le LLM dit "conversation" mais que
le ML était confiant sur une route d'action, le ML l'emporte.
"""

from __future__ import annotations

import json

from ..base import RouterHandler
from ..context import RouterContext, RouterResult
from ..handlers.ml import _log_uncertain

# Seuil d'arbitrage : ML prévaut si conf >= ce seuil et LLM dit "conversation"
_ML_OVERRIDE_THRESHOLD = 0.85


def _parse_eval_json(raw: str) -> dict:
    """Extrait le JSON d'évaluation depuis la réponse brute du LLM — best-effort."""
    try:
        start = raw.index("{")
        end   = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        return {}


class LLMHandler(RouterHandler):
    """
    Fallback LLM — lance EvaluationCrew et applique l'arbitrage ML/KW.

    Arbitrage (règle d'or) :
      Si LLM dit "conversation" mais ML >= 0.85 sur une route d'action → ML prévaut.
      Si LLM dit "conversation" ET kw_shell hint présent → "shell" prévaut.

    Logge les cas incertains ML dans uncertain_cases.jsonl pour l'active learning.
    """

    def handle(self, ctx: RouterContext) -> RouterResult | None:
        # Import ici pour éviter le couplage circulaire au niveau module
        from Mnemo.crew import EvaluationCrew

        eval_result = EvaluationCrew().crew().kickoff(inputs={
            "user_message":     ctx.message,
            "temporal_context": ctx.temporal_context,
        })
        eval_json = _parse_eval_json(eval_result.raw.strip() if eval_result.raw else "")
        llm_route = eval_json.get("route", "conversation")

        # ── Arbitrage ML vs LLM ──────────────────────────────────────────
        ml_route  = ctx._hints.get("ml_route", "conversation")
        ml_conf   = ctx._hints.get("ml_conf", 0.0)
        kw_shell  = ctx._hints.get("kw_shell", False)

        final_route = llm_route

        if llm_route == "conversation":
            if ml_conf >= _ML_OVERRIDE_THRESHOLD and ml_route != "conversation":
                final_route = ml_route
            elif kw_shell:
                final_route = "shell"
            elif ctx._hints.get("kw_plan_strong"):
                # Keyword fort de plan détecté (même dans un message long) :
                # le LLM local est faible et dit souvent "conversation" par défaut.
                # Un keyword fort ("faire un plan", "prépare-moi un plan"...) est
                # un signal sémantique robuste → on l'emporte sur le LLM.
                final_route = "plan"

        # Coercion : web_query non-null implique needs_web = True
        if eval_json.get("web_query") and not eval_json.get("needs_web"):
            eval_json["needs_web"] = True

        # Coercion : plan + complexité "complex" → needs_recon par défaut
        if final_route == "plan" and not eval_json.get("needs_recon"):
            if eval_json.get("complexity") == "complex":
                eval_json["needs_recon"] = True

        # Hint kw_plan_weak + LLM dit "conversation" + message complexe → "plan"
        if final_route == "conversation" and ctx._hints.get("kw_plan_weak"):
            if eval_json.get("complexity") == "complex":
                final_route = "plan"

        eval_json["route"] = final_route

        # Active learning — log si ML était incertain
        if ml_conf < 0.70:
            _log_uncertain(ctx.message, final_route, ml_conf)

        return RouterResult(
            route      = final_route,
            confidence = ml_conf if final_route == ml_route else 0.5,
            handler    = "llm",
            metadata   = eval_json,
        )