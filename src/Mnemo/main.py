#!/usr/bin/env python
import sys
import json
import uuid
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# ── Désactive le prompt interactif de tracing CrewAI ──────────────
# CrewAI stocke la préférence dans /tmp/.local/share/data/.crewai_user.json
# (tmpfs — recréé à chaque run). On pré-remplit le fichier avant tout
# import crewai pour que is_first_execution() retourne False immédiatement.
def _disable_crewai_tracing() -> None:
    try:
        p = Path("/tmp/.local/share/data/.crewai_user.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or not json.loads(p.read_text()).get("first_execution_done"):
            p.write_text(json.dumps({
                "first_execution_done": True,
                "first_execution_at":   datetime.now().timestamp(),
                "trace_consent":        False,
                "user_id":              "mnemo-docker",
                "machine_id":           "mnemo-docker",
            }))
    except Exception:
        pass  # silencieux — ne jamais bloquer le démarrage

_disable_crewai_tracing()

# ── Debug routing — active avec MNEMO_DEBUG=1 ─────────────────────
import os as _os
_DEBUG = _os.getenv("MNEMO_DEBUG", "0") == "1"

def _dbg(msg: str) -> None:
    if _DEBUG:
        print(f"  [DBG] {msg}")


# ── Active learning — collecte les cas incertains pour re-entrainement ──
# uncertain_cases.jsonl dans /data — pas dans /app (read-only)
_UNCERTAIN_LOG = Path("uncertain_cases.jsonl")  # relatif à WORKDIR=/data
_UNCERTAIN_CONF_THRESHOLD = 0.70  # en dessous = cas incertain

def _log_uncertain(message: str, final_route: str, ml_conf: float) -> None:
    """
    Logge les messages ou le ML etait peu confiant (conf < seuil).
    Ces cas seront utilises pour ameliorer le modele au prochain re-train.
    La route finale (decidee par LLM ou keywords) sert de label.
    """
    if ml_conf >= _UNCERTAIN_CONF_THRESHOLD:
        return  # ML etait confiant, pas besoin de loguer
    try:
        import json as _json
        entry = _json.dumps({
            "text":       message,
            "route":      final_route,
            "ml_conf":    round(ml_conf, 3),
            "source":     "active_learning",
        }, ensure_ascii=False)
        with open(_UNCERTAIN_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        _dbg(f"cas incertain logge -> {_UNCERTAIN_LOG.name} (conf={ml_conf:.2f}, label={final_route})")
    except Exception as e:
        _dbg(f"log uncertain echoue : {e}")

# ─────────────────────────────────────────────────────────────

from Mnemo.crew import (
    ConversationCrew, ConsolidationCrew, CuriosityCrew, EvaluationCrew,
    ShellCrew, BriefingCrew, CalendarWriteCrew, SchedulerCrew, NoteWriterCrew,
)
from Mnemo.tools.memory_tools import (
    update_session_memory, load_session_json, SESSIONS_DIR,
    check_and_sync, MARKDOWN_PATH, get_db, compute_hash,
    update_markdown_section, sync_markdown_to_db,
)
from Mnemo.tools.ingest_tools import ingest_file, list_ingested_documents
from Mnemo.tools.calendar_tools import (
    get_temporal_context, get_upcoming_events, format_events_for_prompt,
    get_deadline_context, format_startup_banner, calendar_is_configured,
)
from Mnemo.tools.web_tools import (
    SEARXNG_URL, _DDG_AVAILABLE, web_search, format_results_for_prompt,
)


# ══════════════════════════════════════════════════════════════
# CuriosityCrew — menu de questionnement
# ══════════════════════════════════════════════════════════════

MAX_QUESTIONS = 5

# Schéma de référence : section → liste de champs attendus
# Chaque champ est un tuple (aliases[], question, section_cible, subsection_cible)
# aliases : liste de mots-clés alternatifs — si L'UN d'eux est présent dans la
# section correspondante, le champ est considéré comme rempli.
# Tuple : (aliases, question, section, subsection, label_markdown)
# La clé du dict doit correspondre au titre ## normalisé dans memory.md.
# "préférences" ne correspond à aucun ## → regroupé sous "identité utilisateur".
MEMORY_SCHEMA: dict[str, list[tuple[list[str], str, str, str, str]]] = {
    "identité utilisateur": [
        (["prénom", "nom", "pseudo", "s'appelle", "matt", "name"],
         "Comment tu t'appelles ?",
         "Identité Utilisateur", "Profil de base", "Nom/Pseudo"),
        (["profession", "métier", "développeur", "ingénieur", "designer", "travail"],
         "Quelle est ta profession ou ton domaine d'activité ?",
         "Identité Utilisateur", "Profil de base", "Métier"),
        (["localisation", "ville", "pays", "france", "paris", "région", "fuseau"],
         "Dans quelle ville ou pays tu te trouves ?",
         "Identité Utilisateur", "Profil de base", "Localisation"),
        # Sous "## 🧑 Identité Utilisateur" → ### Préférences & style
        (["style", "communication", "courte", "détaillée", "verbeux", "concis", "direct"],
         "Tu préfères des réponses courtes et directes, ou détaillées ?",
         "Identité Utilisateur", "Préférences & style", "Style de communication"),
    ],
    "identité agent": [
        (["prénom", "nom", "s'appelle", "mitsune", "mnemo", "assistant"],
         "Comment tu veux appeler l'agent ? Il a un nom ?",
         "Identité Agent", "Rôle & personnalité définis", "Nom de l'agent"),
    ],
}


def _extract_section_content(memory_content: str, section_key: str) -> str:
    """
    Extrait le contenu d'une section spécifique dans memory.md.
    Insensible à la casse et aux emojis.
    Retourne le texte entre ce ## et le ## suivant.
    """
    import re
    lines   = memory_content.splitlines()
    content = []
    inside  = False
    for line in lines:
        if line.startswith("## "):
            norm = re.sub(r'^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\s]+', '', line.lstrip("# ").strip()).strip().lower()
            if section_key in norm or norm in section_key:
                inside = True
                continue
            elif inside:
                break
        elif inside:
            content.append(line.lower())
    return "\n".join(content)


# Marqueurs de placeholder — une ligne qui ne contient QUE ça n'est pas une vraie valeur
_PLACEHOLDER_MARKERS = (
    "pas encore renseigné",
    "aucun",
    "aucune",
    "pour l'instant",
    "je dois questionner",
    "je me demande",
)

def _line_is_real_value(line: str, alias: str) -> bool:
    """
    Retourne True si la ligne contenant l'alias est une vraie valeur,
    pas juste un label de template ou un placeholder.
    Exemples :
      "- **Nom/Pseudo** : pas encore renseigné" → False (label + placeholder)
      "- **Nom/Pseudo** : Matt"                 → True
      "Matt, développeur web"                   → True
    """
    line_lower = line.lower()
    # Alias trouvé sur une ligne de placeholder → pas une vraie valeur
    if any(p in line_lower for p in _PLACEHOLDER_MARKERS):
        return False
    # Alias trouvé uniquement dans un label Markdown (entre ** **)
    # ex: "**Nom/Pseudo**" — si la partie après ":" est vide, pas de valeur
    if "**" in line_lower:
        parts = line_lower.split(":", 1)
        if len(parts) == 2:
            value_part = parts[1].strip()
            return bool(value_part) and not any(p in value_part for p in _PLACEHOLDER_MARKERS)
        return False
    return True


def _detect_structural_gaps(memory_content: str) -> list[dict]:
    """
    Détecte les lacunes structurelles dans memory.md en comparant
    le contenu avec MEMORY_SCHEMA.
    - Cherche les aliases dans la section correspondante (pas dans tout le fichier)
    - Ignore les matches sur les labels de template (** **) et les placeholders
    - Insensible à la casse et aux emojis
    - Pure Python — aucun LLM, résultat garanti.
    """
    import re
    gaps = []

    for section_key, fields in MEMORY_SCHEMA.items():
        section_content = _extract_section_content(memory_content, section_key)
        section_present = bool(section_content.strip())

        for (aliases, question, sec, subsec, label) in fields:
            q_id = compute_hash(question)
            found = False
            if section_present:
                for line in section_content.splitlines():
                    # Ignore les headers ### — ce sont des titres, pas des valeurs
                    if line.strip().startswith("#"):
                        continue
                    for alias in aliases:
                        if alias.lower() in line and _line_is_real_value(line, alias):
                            found = True
                            break
                    if found:
                        break

            if not found:
                priority = 1 if not section_present else 2
                gaps.append({
                    "id":         q_id,
                    "question":   question,
                    "section":    sec,
                    "subsection": subsec,
                    "label":      label,
                    "priority":   priority,
                    "type":       "structural",
                })

    return gaps



def _get_skipped_questions() -> list[str]:
    """Retourne les IDs des questions déjà skippées."""
    db   = get_db()
    rows = db.execute("SELECT id FROM curiosity_skipped").fetchall()
    db.close()
    return [r[0] for r in rows]


def _mark_skipped(question_id: str, question: str) -> None:
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO curiosity_skipped (id, question) VALUES (?, ?)",
        (question_id, question)
    )
    db.commit()
    db.close()


def _display_menu(questions: list[dict]) -> None:
    """Affiche le menu de questions généré par le GapDetector."""
    print("\n" + "─" * 55)
    print("🤔 Mnemo a quelques questions pour mieux te connaître :")
    print("─" * 55)
    for i, q in enumerate(questions, start=1):
        print(f"  [{i}] {q['question']}")
    print(f"  [0] Passer")
    print("─" * 55)


def _collect_answers(questions: list[dict]) -> list[dict]:
    """
    Affiche le menu et collecte les réponses de l'utilisateur.
    Retourne une liste de dicts {question, answer, section, subsection}.
    Les questions skippées sont marquées en DB.
    """
    _display_menu(questions)
    answers = []

    for q in questions:
        q_id = q.get("id") or compute_hash(q["question"])
        try:
            raw = input(f"\n  → {q['question']}\n    Toi > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  ⚠️  Questionnaire interrompu.")
            break

        if not raw or raw == "0":
            _mark_skipped(q_id, q["question"])
            print("  (Passé — cette question ne sera plus posée)")
        else:
            answers.append({
                "question":   q["question"],
                "answer":     raw,
                "section":    q.get("section", "Connaissances"),
                "subsection": q.get("subsection", "Général"),
                "label":      q.get("label", ""),
            })

    return answers


def curiosity_session(session_content: str) -> None:
    """
    Lance une session de questionnement proactif post-consolidation.

    Phase 1a — Python pur : détecte les lacunes structurelles (schéma fixe)
    Phase 1b — LLM léger  : détecte les lacunes contextuelles (session récente)
    Phase 2  — Fusion      : merge + dédup + priorité + max 5 questions
    Phase 3  — Menu CLI    : collecte les réponses utilisateur
    Phase 4  — LLM         : QuestionnaireAgent reformule + écrit dans memory.md
    """
    memory_content = MARKDOWN_PATH.read_text(encoding="utf-8", errors="ignore") \
        if MARKDOWN_PATH.exists() else ""

    # ── Phase 1a : trous structurels (Python pur, garanti) ──
    structural_gaps = _detect_structural_gaps(memory_content)
    skipped_ids     = _get_skipped_questions()

    # Filtre immédiatement les questions déjà skippées
    structural_gaps = [
        g for g in structural_gaps
        if g["id"] not in skipped_ids
    ]

    # ── Phase 1b : trous contextuels (LLM, best-effort) ──
    contextual_gaps = []
    db              = get_db()
    skipped_rows    = db.execute("SELECT question FROM curiosity_skipped").fetchall()
    db.close()
    skipped_text    = "\n".join(f"- {r[0]}" for r in skipped_rows) or "Aucune"

    # On ne lance le LLM que s'il reste de la place après les trous structurels
    remaining_slots = MAX_QUESTIONS - len(structural_gaps)
    if remaining_slots > 0 and session_content:
        print("\n🔍 Analyse contextuelle en cours...")
        try:
            structural_summary = "\n".join(
                f"- {g['question']}" for g in structural_gaps
            ) or "Aucun trou structurel détecté."

            result = CuriosityCrew().crew().kickoff(inputs={
                "memory_content":     memory_content[:6000],
                "session_summary":    session_content,
                "skipped_questions":  skipped_text,
                "structural_gaps":    structural_summary,
                "answers_json":       "[]",
            })
            raw = result.raw.strip() if result.raw else ""
            # Extrait le JSON même si le LLM a ajouté du texte autour
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start != -1 and end > start:
                detection = json.loads(raw[start:end])
                if detection.get("has_gaps"):
                    for q in detection.get("questions", [])[:remaining_slots]:
                        q_id = q.get("id") or compute_hash(q.get("question", ""))
                        if q_id not in skipped_ids:
                            q["id"]   = q_id
                            q["type"] = "contextual"
                            contextual_gaps.append(q)
        except Exception as e:
            print(f"  ⚠️  Analyse contextuelle ignorée : {e}")

    # ── Phase 2 : fusion ──
    # Structurel d'abord (priorité 1/2), contextuel ensuite
    all_questions = sorted(structural_gaps, key=lambda q: q.get("priority", 9))
    all_questions += contextual_gaps
    all_questions  = all_questions[:MAX_QUESTIONS]

    if not all_questions:
        print("  ✓ Aucune lacune détectée — mémoire complète pour cette session.")
        return

    # ── Phase 3 : menu CLI ──
    answers = _collect_answers(all_questions)

    # ── Phase 4 : écriture directe Python — pas de LLM ──
    # On écrit les réponses directement sans passer par le LLM
    # pour éviter les hallucinations sur answers_json vide ou mal compris.
    if not answers:
        print("  (Toutes les questions ont été passées)")
        return

    print("\n✍️  Intégration des réponses dans la mémoire...")
    written = 0
    for ans in answers:
        try:
            label   = ans.get("label", "")
            raw_ans = ans["answer"]
            # Formate en ligne structurée si un label est disponible
            # ex: "- **Localisation** : France"
            content = f"- **{label}** : {raw_ans}" if label else raw_ans
            update_markdown_section(
                section    = ans.get("section", "Identité Utilisateur"),
                subsection = ans.get("subsection", "Profil de base"),
                content    = content,
                category   = "identité",
            )
            written += 1
        except Exception as e:
            print(f"  ⚠️  Erreur écriture '{ans.get('question', '?')}' : {e}")

    if written:
        sync_markdown_to_db()
        print(f"  ✅ {written} réponse(s) intégrée(s) dans memory.md + sync SQLite")





def new_session_id() -> str:
    return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _confirm_web_search(web_query: str, backend: str) -> bool:
    """
    Affiche la requête web que l'agent veut envoyer et demande confirmation.
    Retourne True si l'utilisateur confirme, False sinon.
    La query est figée — le LLM ne peut plus la modifier après cette étape.
    """
    print(f"\n  🌐 L'agent veut effectuer une recherche web.")
    print(f"     Requête  : {web_query!r}")
    print(f"     Backend  : {backend}")
    print(f"     ⚠️  Ces données seront envoyées hors de ta machine.")
    try:
        answer = input("     Confirmer l'envoi ? (O/n) > ").strip().lower()
        return answer in ("", "o", "oui", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _confirm_shell_command(shell_command: str) -> bool:
    """
    Affiche la commande shell proposée et demande confirmation explicite.
    La commande est figée — le LLM ne peut plus la modifier après cette étape.
    Retourne True si l'utilisateur tape 'oui' (pas de validation par défaut).
    """
    from Mnemo.tools.shell_whitelist import describe_command_policy
    from Mnemo.tools.shell_tools import validate_command

    print()
    print("  🖥️  L'agent veut exécuter une commande système.")
    print(f"     Commande : {shell_command!r}")

    # Pré-validation — affiche le problème avant même de demander
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


def _parse_eval_json(raw: str) -> dict:
    """Extrait le JSON d'évaluation depuis la réponse brute du LLM — best-effort."""
    try:
        # Extrait toujours le sous-string JSON — même si ça commence par {,
        # il peut y avoir du texte après le } final (ex: "{"route":"x"} Voilà.")
        start = raw.index("{")
        end   = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        return {}


def _handle_clarification(
    eval_json: dict, user_message: str, temporal_ctx: str
) -> tuple[dict, str, str]:
    """
    Interception needs_clarification : pose la question, ré-évalue si réponse.
    Retourne (eval_json, eval_raw, user_message) potentiellement mis à jour.
    """
    if not (eval_json.get("needs_clarification") and eval_json.get("clarification_reason")):
        return eval_json, json.dumps(eval_json, ensure_ascii=False), user_message

    reason = eval_json["clarification_reason"]
    print(f"\n  🤔 Mnemo a besoin d'une précision : {reason}")
    try:
        clarif = input("    Toi > ").strip()
        if clarif:
            user_message = f"{user_message}\n[Précision : {clarif}]"
            eval_result  = EvaluationCrew().crew().kickoff(inputs={
                "user_message":     user_message,
                "temporal_context": temporal_ctx,
            })
            eval_raw  = eval_result.raw.strip()
            eval_json = _parse_eval_json(eval_raw)
    except (EOFError, KeyboardInterrupt):
        pass

    return eval_json, json.dumps(eval_json, ensure_ascii=False), user_message


def _handle_web_confirmation(eval_json: dict) -> tuple[dict, str]:
    """
    Interception needs_web : affiche la query figée, demande confirmation.
    Si refus, désactive needs_web dans eval_json.
    Retourne (eval_json, web_context) — web_context est la chaîne formatée ou "".
    """
    web_context = ""
    if not (eval_json.get("needs_web") and eval_json.get("web_query")):
        return eval_json, web_context

    web_query = eval_json["web_query"]
    backend = "SearXNG" if SEARXNG_URL else \
              "DuckDuckGo" if _DDG_AVAILABLE else "aucun backend configuré"

    if _confirm_web_search(web_query, backend):
        results = web_search(web_query)
        web_context = format_results_for_prompt(results) if results else ""
    else:
        eval_json["needs_web"] = False
        eval_json["web_query"] = None
        print("     Recherche web annulée — réponse depuis la mémoire uniquement.\n")

    return eval_json, web_context


def _extract_shell_command(user_message: str) -> str:
    """
    Quand le LLM n'a pas produit de shell_command malgré route=shell,
    on repose une question ciblée pour extraire uniquement la commande.
    Retourne une chaîne vide si rien de valide n'est trouvé.
    """
    if not user_message:
        return ""
    try:
        import requests as _req
        prompt = (
            f"L'utilisateur demande : \"{user_message}\"\n"
            "Génère UNIQUEMENT la commande shell Linux correspondante, "
            "en utilisant /data comme racine. Une seule commande. "
            "Pas d'explication, pas de markdown. "
            "Si tu ne peux pas déterminer de commande précise, réponds: NULL\n"
            "Commande :"
        )
        # Réutilise le même endpoint Ollama que generate_training_data
        import os as _os
        host  = _os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        model_raw = _os.getenv("MODEL", "ollama/mistral").replace("ollama/", "")
        r = _req.post(
            f"{host}/v1/chat/completions",
            json={"model": model_raw,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 60, "temperature": 0.0},
            timeout=15,
        )
        if r.status_code == 200:
            cmd = r.json()["choices"][0]["message"]["content"].strip()
            cmd = cmd.strip("`").strip()
            if cmd and cmd.upper() != "NULL" and len(cmd) < 200:
                return cmd
    except Exception as e:
        _dbg(f"_extract_shell_command echoue : {e}")
    return ""


def _handle_shell_confirmation(eval_json: dict) -> tuple[dict, str]:
    """
    Interception route=shell : affiche la commande figée, demande confirmation EXPLICITE.
    Si refus ou commande invalide, revert vers route conversation.
    Retourne (eval_json, shell_command_confirmed) — shell_command_confirmed est "" si annulé.
    """
    if eval_json.get("route") != "shell":
        return eval_json, ""

    shell_command = (eval_json.get("shell_command") or "").strip()

    if not shell_command:
        # LLM a dit conversation mais keywords ont corrigé en shell.
        # On tente d'extraire la commande via un prompt ciblé.
        shell_command = _extract_shell_command(eval_json.get("_user_message", ""))
        if shell_command:
            eval_json["shell_command"] = shell_command
        else:
            print("  ⚠️  Route shell mais commande introuvable — redirection conversation.")
            eval_json["route"] = "conversation"
            return eval_json, ""

    if _confirm_shell_command(shell_command):
        print("     Commande confirmee - execution en cours...")
        return eval_json, shell_command
    else:
        print("     Commande annulee - reponse depuis la memoire.")
        eval_json["route"] = "conversation"
        return eval_json, ""


def _route_message(
    eval_json: dict,
    user_message: str,
    session_id: str,
    temporal_ctx: str,
    web_context: str,
) -> str:
    """
    Dispatche vers le bon crew selon eval_json["route"].

    Routes :
      "conversation" (défaut) → ConversationCrew
      "shell"                 → ShellCrew  (stub phase 3.2)
      "calendar"              → CalendarWriteCrew (stub phase 3.3)
      "scheduler"             → SchedulerCrew
    Route inconnue ou absente → conversation silencieux.

    Si web_context non vide (needs_web confirmé) :
      injecté dans les inputs pour toutes les routes — enrichit le contexte avant l'action.
    """
    route = eval_json.get("route", "conversation")
    eval_raw = json.dumps(eval_json, ensure_ascii=False)

    # Pré-fetch calendrier si needs_calendar — évite que le retriever
    # consomme des itérations à appeler GetCalendarTool lui-même
    calendar_context = ""
    if eval_json.get("needs_calendar"):
        try:
            ref_date_str = eval_json.get("reference_date")
            if ref_date_str:
                from datetime import date as _date
                ref_date = _date.fromisoformat(ref_date_str)
                from Mnemo.tools.calendar_tools import get_events_for_date
                cal_events = get_events_for_date(ref_date)
            else:
                # Fenêtre large pour les questions "prochaine session X"
                cal_events = get_upcoming_events(days=21)
            calendar_context = format_events_for_prompt(cal_events) if cal_events else "Aucun événement trouvé."
        except Exception as e:
            calendar_context = f"Erreur calendrier : {e}"

    base_inputs = {
        "user_message":      user_message,
        "evaluation_result": eval_raw,
        "temporal_context":  temporal_ctx,
        "web_context":       web_context,
        "calendar_context":  calendar_context,
        "_web_mode":         eval_json.get("_web_mode", False),
    }

    if route == "shell":
        shell_command = eval_json.get("shell_command", "")
        return ShellCrew().run({
            **base_inputs,
            "shell_command": shell_command,
        })

    if route == "note":
        return NoteWriterCrew().run({"user_message": user_message})

    if route == "calendar":
        return CalendarWriteCrew().run({**base_inputs})

    if route == "scheduler":
        return SchedulerCrew().run({**base_inputs})

    # "conversation" ou route inconnue → ConversationCrew (défaut silencieux)
    result = ConversationCrew().crew().kickoff(inputs={
        **base_inputs,
        "session_id":    session_id,
        "memory_context": "",
    })
    return result.raw


# ══════════════════════════════════════════════════════════════════
# Routing hybride : keywords + ML + LLM fallback
# ══════════════════════════════════════════════════════════════════

_SHELL_KEYWORDS = [
    "liste les fichiers", "liste les dossiers", "liste le dossier",
    "lister les fichiers", "lister les dossiers",
    "qu est-ce qu il y a dans", "contenu du dossier",
    "montre-moi les fichiers", "montre moi les fichiers",
    "va dans le dossier", "va fouiller dans",
    "lis le fichier", "lit le fichier", "lire le fichier",
    "affiche le fichier", "afficher le fichier",
    "montre-moi le fichier", "montre moi le fichier",
    "cherche les fichiers", "trouve les fichiers",
    "find /data", "ls /data", "cat /data",
    "cree le dossier", "cree un dossier",
    "cree le fichier", "cree un fichier",
    "supprime le fichier", "supprime le dossier",
    "deplace le fichier", "copie le fichier",
    "lance le script", "execute le script",
    "commande shell", "commande systeme", "en shell", "via shell",
    "liste moi les", "liste-moi les",
]

def _detect_shell_intent(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _SHELL_KEYWORDS)


# Keywords forts → bypass LLM déterministe (non ambigus)
_SCHEDULER_KEYWORDS_STRONG = [
    "rappelle-moi", "rappelle moi",
    "planifie", "planifier",
    "chaque lundi", "chaque mardi", "chaque mercredi", "chaque jeudi",
    "chaque vendredi", "chaque samedi", "chaque dimanche",
    "tous les lundis", "tous les mardis", "tous les mercredis",
    "tous les jeudis", "tous les vendredis",
    "chaque semaine", "chaque jour", "tous les jours",
    "tous les matins", "chaque matin",
    "annule le rappel", "annule la tâche", "supprime le rappel",
    "liste mes rappels", "liste mes tâches planifiées",
    "quels sont mes rappels",
    "programme le rappel",
    "programmer un rappel", "programmer une tâche",
]

# Keywords faibles → hint pour le ML uniquement (restent ambigus sans ML)
_SCHEDULER_KEYWORDS_WEAK = [
    "programme un ", "programme une ", "programme ce ", "programme cette ",
    "programme mon ", "programme ma ",
    "programmer un ", "programmer une ", "programmer ce ", "programmer cette ",
    "dans 1 jour", "dans 2 jours", "dans 3 jours",
    "demain matin", "demain soir",
]

def _detect_scheduler_intent(msg: str) -> tuple[bool, bool]:
    """Retourne (strong, weak) — strong = bypass LLM, weak = hint ML."""
    m = msg.lower()
    strong = any(kw in m for kw in _SCHEDULER_KEYWORDS_STRONG)
    weak   = any(kw in m for kw in _SCHEDULER_KEYWORDS_WEAK)
    return strong, weak


_CALENDAR_WRITE_KEYWORDS = [
    # Création — impératif direct
    "ajoute un événement", "ajoute un evenement", "ajoute un rdv", "ajoute un rendez-vous",
    "crée un événement", "cree un evenement", "crée un rdv", "crée un rendez-vous",
    "nouveau rendez-vous", "nouvel événement", "nouvel evenement",
    "mets un événement", "mets un evenement", "met un événement", "met un evenement",
    "planifie un événement", "planifie un evenement", "planifie un rendez-vous",
    # Création — vers agenda/calendrier
    "ajoute à mon agenda", "ajoute dans mon agenda", "ajoute à l'agenda",
    "inscris dans mon agenda", "inscris à mon agenda",
    "bloque dans mon calendrier", "bloque le créneau", "bloque ce créneau",
    # Suppression — impératif direct
    "supprime l'événement", "supprime l'evenement", "supprime le rendez-vous", "supprime le rdv",
    "annule l'événement", "annule l'evenement", "annule le rendez-vous", "annule le rdv",
    "efface l'événement", "efface l'evenement", "efface le rendez-vous",
    "enlève l'événement", "enleve l'evenement", "enlève le rendez-vous",
    # Suppression — infinitif + lieu ("tu peux me le supprimer du calendrier")
    "supprimer du calendrier", "supprimer de mon calendrier", "supprimer de l'agenda",
    "supprimer de mon agenda", "enlever du calendrier", "enlever de l'agenda",
    "retirer du calendrier", "retirer de l'agenda", "effacer du calendrier",
    # Modification — impératif / infinitif
    "décale mon rendez-vous", "decale mon rendez-vous", "décale l'événement", "decale l'evenement",
    "modifie mon rendez-vous", "modifie l'événement", "modifie l'evenement",
    "déplace mon rendez-vous", "deplace mon rendez-vous", "déplace l'événement",
    "repousse le rendez-vous", "repousse l'événement", "avance le rendez-vous",
    "change l'heure de mon rendez-vous", "change la date de mon rendez-vous",
]

# Détection par co-occurrence : verbe d'action + mot contexte calendrier
# Couvre les formes naturelles : "tu peux supprimer cet événement ?"
_CAL_ACTION_VERBS = [
    # Infinitifs
    "supprimer", "effacer", "enlever", "retirer", "annuler",
    "ajouter", "créer", "creer", "insérer", "inserer", "planifier",
    "modifier", "changer", "déplacer", "deplacer", "décaler", "decaler",
    "repousser", "avancer",
    # Formes conjuguées (impératif / présent)
    "supprime", "efface", "enlève", "enleve", "retire", "annule",
    "ajoute", "crée", "cree", "insère", "insere", "planifie",
    "modifie", "déplace", "deplace", "décale", "decale",
    "repousse", "avance", "mets",
]
_CAL_CONTEXT_WORDS = [
    "calendrier", "agenda",
    "événement", "evenement", "évènement", "evènement",
    "event", "l'event", "cet event",
    "rdv", "rendez-vous", "rendez vous",
    "créneau", "creneau",
]

def _detect_calendar_write_intent(msg: str) -> bool:
    m = msg.lower()
    # Correspondance directe (phrases fixes)
    if any(kw in m for kw in _CALENDAR_WRITE_KEYWORDS):
        return True
    # Co-occurrence : verbe d'action + mot calendrier (formes naturelles)
    has_verb = any(v in m for v in _CAL_ACTION_VERBS)
    has_ctx  = any(c in m for c in _CAL_CONTEXT_WORDS)
    return has_verb and has_ctx


_NOTE_KEYWORDS = [
    "note que", "notes que", "retiens que", "retiens bien que",
    "mémorise que", "mémorise ça", "mémorise ceci",
    "n'oublie pas que", "n'oublie pas ça",
    "souviens-toi que", "souviens toi que",
    "garde en mémoire", "garde ça en mémoire",
    "enregistre que", "enregistre ceci", "enregistre ça",
    "ajoute à ma mémoire", "ecris dans ma memoire", "écris dans ma mémoire",
    "ajoute à mes notes", "ajoute dans mes notes",
    "important à noter", "important a noter",
    "à noter :", "a noter :",
]

def _detect_note_intent(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _NOTE_KEYWORDS)


_ROUTER_MODEL = None

def _load_router_model():
    global _ROUTER_MODEL
    if _ROUTER_MODEL is not None:
        return _ROUTER_MODEL
    # Cherche d'abord dans /data (retrain sans rebuild), fallback /app
    model_path = Path("router_model.joblib")
    if not model_path.exists():
        model_path = Path(__file__).parent / "router_model.joblib"
    if not model_path.exists():
        return None
    try:
        import joblib
        _ROUTER_MODEL = joblib.load(model_path)
        n = _ROUTER_MODEL.get("n_train", "?")
        print(f"  [router ML] modele charge ({n} exemples)")
        return _ROUTER_MODEL
    except Exception as e:
        print(f"  [router ML] echec : {e}")
        return None


def _ml_detect_intent(message: str) -> tuple:
    """Retourne (route, confidence). confidence=0.0 si modele absent."""
    md = _load_router_model()
    if md is None:
        return "conversation", 0.0
    try:
        pipeline = md["pipeline"]
        proba    = pipeline.predict_proba([message])[0]
        max_idx = proba.argmax()
        conf = float(proba[max_idx])        
        # Si le ML est vraiment perdu (ex: < 0.40), on ne lui fait pas confiance du tout
        if conf < 0.40:
            return "conversation", 0.0
        return md["routes"][max_idx], conf
    except Exception:
        return "conversation", 0.0


def handle_message(user_message: str, session_id: str) -> str:
    """
    Pipeline d'arbitrage haute-fidélité :
    Priorité 1 : Keywords critiques (Déterminisme)
    Priorité 2 : ML à haute confiance (Vitesse)
    Priorité 3 : LLM (Nuance sémantique)
    Priorité 4 : Arbitrage ML vs LLM (Sécurité)
    """
    temporal_ctx = get_temporal_context()
    
    # --- 1. PRE-CHECK : Keywords & ML ---
    kw_shell                    = _detect_shell_intent(user_message)
    kw_scheduler_strong, kw_scheduler_weak = _detect_scheduler_intent(user_message)
    kw_note                     = _detect_note_intent(user_message)
    kw_calendar                 = _detect_calendar_write_intent(user_message)
    ml_route, ml_conf = _ml_detect_intent(user_message)

    _dbg(f"[Check] KW: shell={kw_shell}, sched_strong={kw_scheduler_strong}, sched_weak={kw_scheduler_weak}, note={kw_note}, cal={kw_calendar} | ML: {ml_route} ({ml_conf:.2f})")

    # Routes déterministes — bypass LLM direct pour les signaux non ambigus
    if kw_note:
        _dbg("SKIP LLM activé -> Route retenue : note (keyword)")
        eval_json = {
            "route": "note",
            "needs_memory": False,
            "needs_web": False,
            "needs_clarification": False,
        }
        return _route_message(eval_json, user_message, temporal_ctx, "", session_id)

    if kw_calendar:
        _dbg("SKIP LLM activé -> Route retenue : calendar (keyword)")
        eval_json = {
            "route": "calendar",
            "needs_memory": False,
            "needs_web": False,
            "needs_clarification": False,
        }
        return _route_message(eval_json, user_message, temporal_ctx, "", session_id)

    if kw_scheduler_strong:
        _dbg("SKIP LLM activé -> Route retenue : scheduler (keyword fort)")
        eval_json = {
            "route": "scheduler",
            "needs_memory": False,
            "needs_web": False,
            "needs_clarification": False,
        }
        return _route_message(eval_json, user_message, temporal_ctx, "", session_id)

    # --- 2. DÉCISION DU SKIP (Bypass LLM pour la performance) ---
    # On bypass si le ML est ultra-sûr OU si Keywords faibles + ML concordent
    should_skip_llm = (
        (ml_conf >= 0.95) or
        (kw_shell and ml_route == "shell" and ml_conf >= 0.80) or
        (kw_scheduler_weak and ml_route == "scheduler" and ml_conf >= 0.80) or
        (kw_calendar and ml_route == "calendar" and ml_conf >= 0.80)
    )

    if should_skip_llm:
        _dbg(f"SKIP LLM activé -> Route retenue : {ml_route}")
        eval_json = {
            "route": ml_route,
            "needs_memory": ml_route == "conversation",
            "needs_web": False,
            "needs_clarification": False,
            "shell_command": None # Sera extrait plus tard si besoin
        }
    else:
        # --- 3. EVALUATION LLM (Le juge sémantique) ---
        _dbg("Lancement de l'EvaluationCrew (LLM)...")
        eval_result = EvaluationCrew().crew().kickoff(inputs={
            "user_message": user_message,
            "temporal_context": temporal_ctx,
        })
        eval_json = _parse_eval_json(eval_result.raw.strip())
        llm_route = eval_json.get("route", "conversation")

        # --- 4. ARBITRAGE (La Correction) ---
        # Ici on gère ton cas d'école : LLM dit "conversation" (RAG) 
        # mais le ML voit des fichiers (Shell)
        
        final_route = llm_route
        
        # Règle d'or : Si le LLM veut discuter mais que le ML est formel sur une action
        if llm_route == "conversation":
            if ml_conf >= 0.85 and ml_route != "conversation":
                final_route = ml_route
                _dbg(f"ARBITRAGE : ML redresse {llm_route} -> {ml_route} (Conf: {ml_conf:.2f})")
            
            # Priorité absolue aux mots-clés techniques (ls, rm, cd...)
            elif kw_shell:
                final_route = "shell"
                _dbg("ARBITRAGE : Keyword Shell prioritaire sur LLM Conversation")
                
        eval_json["route"] = final_route

    # --- 5. POST-TRAITEMENT (Clarification / Web / Shell) ---
    # Clarification si besoin (ex: "quel fichier ?")
    eval_json, _, user_message = _handle_clarification(eval_json, user_message, temporal_ctx)
    
    # Recherche Web si le LLM l'a demandé ou si route conversation
    eval_json, web_context = _handle_web_confirmation(eval_json)
    
    # Confirmation de commande Shell si route == shell
    eval_json, _ = _handle_shell_confirmation(eval_json)

    # Sauvegarde du message pour l'extraction de commande
    eval_json["_user_message"] = user_message
    final_route = eval_json.get("route", "conversation")

    # Log pour futur entraînement (si ml_conf est bas, on veut apprendre de cette phrase)
    if ml_conf < 0.70:
        _log_uncertain(user_message, final_route, ml_conf)

    # --- 6. ROUTAGE FINAL VERS LES AGENTS ---
    _dbg(f"ROUTE FINALE -> {final_route}")
    response = _route_message(
        eval_json, user_message, session_id, temporal_ctx, web_context
    )

    update_session_memory(session_id, user_message, response)
    return response

def end_session(session_id: str) -> tuple:
    """
    Consolide la session terminée en mémoire long terme.
    Retourne (consolidation_summary, session_messages_text).
    """
    session = load_session_json(session_id)
    if not session:
        return "Session vide, rien à consolider.", ""

    # Extrait le texte brut des messages pour CuriosityCrew
    messages = session if isinstance(session, list) else session.get("messages", [])
    session_text = "\n".join(
        f"{m.get('role','?').upper()} : {m.get('content','')}"
        for m in messages
        if m.get("content", "").strip()
    )

    result = ConsolidationCrew().crew().kickoff(inputs={
        "session_json":     json.dumps(session, ensure_ascii=False, indent=2),
        "temporal_context": get_temporal_context(),
    })

    # Marque la session comme consolidée
    (SESSIONS_DIR / f"{session_id}.done").touch()
    return result.raw, session_text


# ══════════════════════════════════════════════════════════════
# Consolidation des sessions orphelines (CTRL+C précédents)
# ══════════════════════════════════════════════════════════════

def consolidate_orphan_sessions():
    """Consolide les sessions JSON non traitées des runs précédents."""
    orphans = [
        f for f in SESSIONS_DIR.glob("*.json")
        if not f.stem.endswith(".broken")
        and not (SESSIONS_DIR / f"{f.stem}.done").exists()
    ]
    if not orphans:
        return
    print(f"🔍 {len(orphans)} session(s) non consolidée(s) trouvée(s).")
    for path in orphans:
        session_id = path.stem
        print(f"   ↳ Consolidation de {session_id}...")

        # Vérifie que le fichier est lisible avant de lancer le crew
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw:
            print(f"   ⚠️  Session vide, ignorée et marquée comme traitée.")
            (SESSIONS_DIR / f"{session_id}.done").touch()
            continue

        try:
            summary, _ = end_session(session_id)
            print(f"   ✅ OK — {summary[:80]}...")
        except Exception as e:
            print(f"   ❌ Échec : {e}")
            # On marque quand même comme done pour éviter de boucler indéfiniment
            (SESSIONS_DIR / f"{session_id}.done").touch()
            print(f"   ↳ Session marquée comme traitée pour ne pas bloquer au prochain démarrage.")


# ══════════════════════════════════════════════════════════════
# Entrypoints CrewAI (run / train / replay / test)
# ══════════════════════════════════════════════════════════════

def _show_briefing_if_fresh() -> None:
    """
    Affiche le contenu de briefing.md au démarrage si le fichier
    a été généré aujourd'hui (par le scheduler) et n'a pas encore été lu.
    Marque le fichier comme lu en ajoutant briefing.read à côté.
    """
    briefing_path = MARKDOWN_PATH.parent / "briefing.md"
    read_flag     = MARKDOWN_PATH.parent / "briefing.read"

    if not briefing_path.exists():
        return

    # Vérifie que le fichier date d'aujourd'hui
    from datetime import date as _date
    import os as _os
    mtime = _os.path.getmtime(briefing_path)
    file_date = _date.fromtimestamp(mtime)
    if file_date != _date.today():
        return

    # Déjà lu aujourd'hui ?
    if read_flag.exists():
        read_flag_date = _date.fromtimestamp(_os.path.getmtime(read_flag))
        if read_flag_date == _date.today():
            return

    try:
        content = briefing_path.read_text(encoding="utf-8")
        print("\n" + "─" * 50)
        print(content.strip())
        print("─" * 50 + "\n")
        # Marque comme lu
        read_flag.touch()
    except Exception:
        pass  # Silencieux si lecture impossible



def run():
    """
    Point d'entrée principal — appelé par `crewai run`.
    Lance une session de conversation interactive en CLI.
    """
    # 1. Vérifie la cohérence de memory.md avec la DB
    check_and_sync()

    # 2. Rattrape les sessions orphelines des runs précédents
    consolidate_orphan_sessions()

    session_id = new_session_id()
    print(f"\n🧠 Agent démarré — session : {session_id}")
    print("Tape 'exit' pour terminer proprement.")

    # Bannière calendrier — affiche les événements urgents si configuré
    if calendar_is_configured():
        events = get_upcoming_events(days=3)
        banner = format_startup_banner(events)
        if banner:
            print(banner)

        # Message d'ouverture — aujourd'hui + demain uniquement
        urgent_today    = [e for e in events if e["is_today"]]
        urgent_tomorrow = [e for e in events if e["is_tomorrow"]]

        parts = []
        if urgent_today:
            titles = ", ".join(e["title"] for e in urgent_today)
            parts.append(f"aujourd'hui : {titles}")
        if urgent_tomorrow:
            titles = ", ".join(e["title"] for e in urgent_tomorrow)
            parts.append(f"demain : {titles}")

        if parts:
            print(f"\n💬 Mnemo : Au fait, tu as {' | '.join(parts)}.")
            print("   Tu veux qu'on en parle ou on avance sur autre chose ?")
    print()

    # Briefing matinal — affiché si généré aujourd'hui par le scheduler
    _show_briefing_if_fresh()

    # Premier lancement — memory.md vierge → questionnaire d'initialisation
    memory_content = MARKDOWN_PATH.read_text(encoding="utf-8", errors="ignore") \
        if MARKDOWN_PATH.exists() else ""
    structural_gaps = _detect_structural_gaps(memory_content)
    skipped_ids     = _get_skipped_questions()
    unfilled_gaps   = [g for g in structural_gaps if g["id"] not in skipped_ids]
    if unfilled_gaps:
        print("👋 Bienvenue ! Avant de commencer, quelques questions pour initialiser ta mémoire.\n")
        curiosity_session("")
        print()

    try:
        while True:
            try:
                user_input = input("Toi > ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                break

            try:
                response = handle_message(user_input, session_id)
                print(f"\nAgent > {response}\n")
            except Exception as e:
                print(f"⚠️ Erreur lors du traitement : {e}")
                raise e

    except KeyboardInterrupt:
        print("\n\n⚠️  Interruption détectée.")

    finally:
        # S'exécute toujours — CTRL+C, exit normal, crash Python
        print("⏳ Consolidation de la session en cours...")
        session_summary = ""
        try:
            session_summary, session_text = end_session(session_id)
            print(f"✅ Session consolidée :\n{session_summary}\n")
        except Exception as e:
            print(f"❌ Consolidation échouée : {e}")
            print(f"   Session sauvegardée dans sessions/{session_id}.json")
            print(f"   Elle sera consolidée automatiquement au prochain démarrage.")

        # Questionnement proactif — déclenché même si le résumé est vide
        # (les trous structurels sont détectés par Python, pas par le LLM)
        try:
            curiosity_session(session_text or session_summary or "")
        except Exception as e:
            print(f"  ⚠️  Questionnement ignoré : {e}")


def train():
    """Entraîne le crew sur N itérations."""
    inputs = {
        "user_message":      "AI LLMs",
        "session_id":        "train_session",
        "evaluation_result": "",
        "memory_context":    "",
    }
    try:
        ConversationCrew().crew().train(
            n_iterations=int(sys.argv[1]),
            filename=sys.argv[2],
            inputs=inputs
        )
    except Exception as e:
        raise Exception(f"Erreur lors du training : {e}")


def replay():
    """Rejoue l'exécution du crew depuis une task spécifique."""
    try:
        ConversationCrew().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"Erreur lors du replay : {e}")


def test():
    """Teste l'exécution du crew et retourne les résultats."""
    inputs = {
        "user_message":      "AI LLMs",
        "session_id":        "test_session",
        "evaluation_result": "",
        "memory_context":    "",
        "current_year":      str(datetime.now().year),
    }
    try:
        ConversationCrew().crew().test(
            n_iterations=int(sys.argv[1]),
            openai_model_name=sys.argv[2],
            inputs=inputs
        )
    except Exception as e:
        raise Exception(f"Erreur lors du test : {e}")


# ══════════════════════════════════════════════════════════════
# Commandes Phase 2 — Ingestion de documents
# ══════════════════════════════════════════════════════════════

def ingest(file_path: str) -> None:
    """
    Ingère un fichier PDF dans la base de connaissances.
    Appelé via : crewai run -- ingest chemin/vers/fichier.pdf
    Ou directement : python -m Mnemo.main ingest fichier.pdf
    """
    path = Path(file_path)
    if not path.exists():
        print(f"❌ Fichier introuvable : {file_path}")
        return

    ext = path.suffix.lower()
    if ext not in (".pdf", ".docx", ".txt", ".md"):
        print(f"❌ Format non supporté : {path.suffix}")
        print("   Formats supportés : .pdf, .docx, .txt, .md")
        return

    print(f"📄 Ingestion de {path.name}...")
    try:
        result = ingest_file(path)
    except ImportError as e:
        print(f"❌ Dépendance manquante : {e}")
        return
    except Exception as e:
        print(f"❌ Erreur lors de l'ingestion : {e}")
        raise

    if result["status"] == "already_ingested":
        print(f"ℹ️  {path.name} est déjà dans la base (même contenu). Rien à faire.")
    elif result["status"] == "empty":
        print(f"⚠️  {path.name} ne contient pas de texte extractible ({result['pages']} pages).")
        print("   Le fichier est peut-être scanné (image). OCR non supporté pour l'instant.")
    else:
        print(f"✅ Ingestion terminée !")
        print(f"   Fichier  : {result['filename']}")
        print(f"   Pages    : {result['pages']}")
        print(f"   Chunks   : {result['chunks']}")
        print(f"   ID doc   : {result['doc_id'][:12]}...")


def debug_curiosity() -> None:
    """
    Déclenche le questionnement directement sans passer par une session complète.
    Utile pour tester CuriosityCrew en isolation.
    Usage : python -m Mnemo.main curiosity
    """
    print("🧪 Mode debug — déclenchement direct du questionnaire\n")

    # Affiche l'état de memory.md
    memory_content = MARKDOWN_PATH.read_text(encoding="utf-8", errors="ignore") \
        if MARKDOWN_PATH.exists() else ""
    print(f"📄 memory.md : {len(memory_content)} caractères")

    # Détection structurelle
    structural = _detect_structural_gaps(memory_content)
    skipped    = _get_skipped_questions()
    structural = [g for g in structural if g["id"] not in skipped]
    print(f"🔍 Trous structurels détectés : {len(structural)}")
    for g in structural:
        print(f"   [{g['priority']}] {g['question']}")

    print(f"🚫 Questions skippées en DB : {len(skipped)}")
    print()

    # Lance le questionnaire avec un résumé de test
    curiosity_session("Session de debug — test du questionnement proactif.")



    """Affiche la liste des documents ingérés."""
    docs = list_ingested_documents()
    if not docs:
        print("📚 Aucun document ingéré pour l'instant.")
        print("   Lance : mnemo ingest fichier.pdf")
        return
    print(f"📚 {len(docs)} document(s) ingéré(s) :\n")
    for doc in docs:
        print(f"  • {doc['filename']}")
        print(f"    Pages : {doc['pages']} — Chunks : {doc['chunks']} — Ingéré le : {doc['ingested_at'][:10]}")


# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] in ("run", "start"):
            run()
        elif sys.argv[1] == "train":
            train()
        elif sys.argv[1] == "replay":
            replay()
        elif sys.argv[1] == "test":
            test()
        elif sys.argv[1] == "ingest":
            if len(sys.argv) < 3:
                print("Usage : python -m Mnemo.main ingest <fichier.pdf>")
            else:
                ingest(sys.argv[2])
        elif sys.argv[1] == "docs":
            list_docs()
        elif sys.argv[1] == "curiosity":
            debug_curiosity()
        elif sys.argv[1] == "init_db":
            from Mnemo.init_db import init_db, migrate_db
            init_db()
            migrate_db()
        else:
            print(f"Commande inconnue : {sys.argv[1]}")
            print("Commandes disponibles : run, train, replay, test, ingest, docs, curiosity, init_db")
    else:
        run()