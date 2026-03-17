"""
Middleware de confirmation — couche entre le routing et le dispatch.

Intercepte les RouterResult qui nécessitent une validation humaine :
  - needs_clarification → pose la question, ré-évalue
  - needs_web           → affiche la query figée, demande confirmation
  - route = shell       → affiche la commande, demande confirmation EXPLICITE

Retourne un ConfirmationResult qui contient le RouterResult (éventuellement
mis à jour) + le contexte web et la commande shell confirmés.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .context import RouterContext, RouterResult


@dataclass
class ConfirmationResult:
    result:        RouterResult
    user_message:  str          # peut être enrichi par la clarification
    web_context:   str = ""
    shell_command: str = ""


# ── Confirmation web ──────────────────────────────────────────────────────────

def _confirm_web_search(web_query: str, backend: str) -> bool:
    print(f"\n  🌐 L'agent veut effectuer une recherche web.")
    print(f"     Requête  : {web_query!r}")
    print(f"     Backend  : {backend}")
    print(f"     ⚠️  Ces données seront envoyées hors de ta machine.")
    try:
        answer = input("     Confirmer l'envoi ? (O/n) > ").strip().lower()
        return answer in ("", "o", "oui", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _handle_web_confirmation(result: RouterResult) -> tuple[RouterResult, str]:
    """
    Interception needs_web : affiche la query figée, demande confirmation.
    Si refus, désactive needs_web dans metadata.
    Retourne (result, web_context).
    """
    from Mnemo.tools.web_tools import SEARXNG_URL, _DDG_AVAILABLE, web_search, format_results_for_prompt

    meta = result.metadata
    if not (meta.get("needs_web") and meta.get("web_query")):
        return result, ""

    web_query = meta["web_query"]
    backend   = "SearXNG" if SEARXNG_URL else \
                "DuckDuckGo" if _DDG_AVAILABLE else "aucun backend configuré"

    if _confirm_web_search(web_query, backend):
        results     = web_search(web_query)
        web_context = format_results_for_prompt(results) if results else ""
    else:
        meta["needs_web"] = False
        meta["web_query"] = None
        print("     Recherche web annulée — réponse depuis la mémoire uniquement.\n")
        web_context = ""

    return result, web_context


# ── Confirmation shell ────────────────────────────────────────────────────────

def _confirm_shell_command(shell_command: str) -> bool:
    from Mnemo.tools.shell_whitelist import describe_command_policy
    from Mnemo.tools.shell_tools import validate_command

    print()
    print("  🖥️  L'agent veut exécuter une commande système.")
    print(f"     Commande : {shell_command!r}")

    validation = validate_command(shell_command)
    if not validation:
        print(f"     ❌ Commande refusée par la whitelist : {validation.reason}")
        return False

    print("     ⚠️  Cette commande sera exécutée sur le système de fichiers /data.")
    print("     Tape 'oui' pour confirmer (toute autre réponse annule).")
    try:
        answer = input("     Confirmer ? > ").strip().lower()
        return answer in ("oui", "o", "yes", "y")
    except (EOFError, KeyboardInterrupt):
        return False


def _extract_shell_command(user_message: str) -> str:
    """
    Quand le LLM n'a pas produit de shell_command malgré route=shell,
    repose une question ciblée via Ollama pour extraire la commande.
    Retourne "" si rien de valide.
    """
    if not user_message:
        return ""
    try:
        import requests as _req
        host      = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        model_raw = os.getenv("MODEL", "ollama/mistral").replace("ollama/", "")
        prompt = (
            f"L'utilisateur demande : \"{user_message}\"\n"
            "Génère UNIQUEMENT la commande shell Linux correspondante, "
            "en utilisant /data comme racine. Une seule commande. "
            "Pas d'explication, pas de markdown. "
            "Si tu ne peux pas déterminer de commande précise, réponds: NULL\n"
            "Commande :"
        )
        r = _req.post(
            f"{host}/v1/chat/completions",
            json={"model": model_raw,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 60, "temperature": 0.0},
            timeout=15,
        )
        if r.status_code == 200:
            cmd = r.json()["choices"][0]["message"]["content"].strip().strip("`").strip()
            if cmd and cmd.upper() != "NULL" and len(cmd) < 200:
                return cmd
    except Exception:
        pass
    return ""


def _handle_shell_confirmation(
    result: RouterResult, user_message: str
) -> tuple[RouterResult, str]:
    """
    Interception route=shell : affiche la commande figée, demande confirmation.
    Si refus ou commande introuvable, revert vers conversation.
    Retourne (result, shell_command_confirmed).
    """
    if result.route != "shell":
        return result, ""

    shell_command = (result.metadata.get("shell_command") or "").strip()

    if not shell_command:
        shell_command = _extract_shell_command(user_message)
        if shell_command:
            result.metadata["shell_command"] = shell_command
        else:
            print("  ⚠️  Route shell mais commande introuvable — redirection conversation.")
            result = RouterResult(
                "conversation", result.confidence, result.handler, result.metadata
            )
            return result, ""

    if _confirm_shell_command(shell_command):
        print("     Commande confirmée — exécution en cours...")
        return result, shell_command
    else:
        print("     Commande annulée — réponse depuis la mémoire.")
        result = RouterResult(
            "conversation", result.confidence, result.handler, result.metadata
        )
        return result, ""


# ── Clarification ─────────────────────────────────────────────────────────────

def _handle_clarification(
    result: RouterResult, user_message: str, temporal_ctx: str
) -> tuple[RouterResult, str]:
    """
    Interception needs_clarification : pose la question, ré-évalue.
    Retourne (result, user_message) potentiellement mis à jour.
    """
    meta = result.metadata
    if not (meta.get("needs_clarification") and meta.get("clarification_reason")):
        return result, user_message

    reason = meta["clarification_reason"]
    print(f"\n  🤔 Mnemo a besoin d'une précision : {reason}")
    try:
        clarif = input("    Toi > ").strip()
        if clarif:
            from Mnemo.crew import EvaluationCrew
            from .handlers.llm import _parse_eval_json

            user_message = f"{user_message}\n[Précision : {clarif}]"
            eval_result  = EvaluationCrew().crew().kickoff(inputs={
                "user_message":     user_message,
                "temporal_context": temporal_ctx,
            })
            eval_json = _parse_eval_json(eval_result.raw.strip() if eval_result.raw else "")
            result = RouterResult(
                route      = eval_json.get("route", result.route),
                confidence = result.confidence,
                handler    = "llm",
                metadata   = eval_json,
            )
    except (EOFError, KeyboardInterrupt):
        pass

    return result, user_message


# ── Point d'entrée principal ──────────────────────────────────────────────────

def run_confirmation_middleware(
    result: RouterResult,
    user_message: str,
    temporal_ctx: str,
) -> ConfirmationResult:
    """
    Applique séquentiellement : clarification → web → shell.
    Retourne un ConfirmationResult prêt pour dispatch().
    """
    result, user_message = _handle_clarification(result, user_message, temporal_ctx)
    result, web_context  = _handle_web_confirmation(result)
    result, shell_cmd    = _handle_shell_confirmation(result, user_message)

    return ConfirmationResult(
        result        = result,
        user_message  = user_message,
        web_context   = web_context,
        shell_command = shell_cmd,
    )