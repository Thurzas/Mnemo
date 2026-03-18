from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
import os

from Mnemo.tools.memory_tools import (
    RetrieveMemoryTool,
    GetSessionMemoryTool,
    UpdateMarkdownTool,
    SyncMemoryDbTool,
    ListDocumentsTool,
)
from Mnemo.tools.calendar_tools import GetCalendarTool
from Mnemo.tools.web_tools import WebSearchTool

MODEL    = os.getenv("MODEL", "ollama/mistral")
API_BASE = os.getenv("API_BASE", "http://localhost:11434")

def _llm(temperature: float = 0.0) -> LLM:
    return LLM(model=MODEL, base_url=API_BASE, temperature=temperature)


# ══════════════════════════════════════════════════════════════
# Conversation Crew — tourne à chaque message
# ══════════════════════════════════════════════════════════════

@CrewBase
class EvaluationCrew:
    """
    Crew léger — tâche unique : évalue le message et produit le JSON d'évaluation.
    Séparé de ConversationCrew pour permettre l'interception entre evaluate et retrieve
    (confirmation web, needs_clarification, etc.).
    """
    agents_config = "config/conversation_agents.yaml"
    tasks_config  = "config/evaluation_tasks.yaml"

    @agent
    def evaluator(self) -> Agent:
        return Agent(
            config=self.agents_config["evaluator"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.0),
        )

    @task
    def evaluate_task(self) -> Task:
        return Task(config=self.tasks_config["evaluate_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )


@CrewBase
class ConversationCrew:
    """
    Crew principal — retrieve + main.
    Reçoit evaluation_result déjà validé (après confirmation web si besoin).
    """
    agents_config = "config/conversation_agents.yaml"
    tasks_config  = "config/conversation_tasks.yaml"

    @agent
    def memory_retriever(self) -> Agent:
        return Agent(
            config=self.agents_config["memory_retriever"],
            verbose=False,
            allow_delegation=False,
            tools=[RetrieveMemoryTool(profile="conversation"), GetSessionMemoryTool(), ListDocumentsTool(), GetCalendarTool(), WebSearchTool()],
            max_iter=8,   # session + mémoire + calendrier + web = jusqu'à 4 appels, marge incluse
            llm=_llm(0.0),
        )

    @agent
    def main_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["main_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=3,
            llm=_llm(0.5),
        )

    @task
    def retrieve_task(self) -> Task:
        return Task(config=self.tasks_config["retrieve_task"])

    @task
    def main_task(self) -> Task:
        return Task(config=self.tasks_config["main_task"], context=[self.retrieve_task()])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )


# ══════════════════════════════════════════════════════════════
# Consolidation Crew — tourne une fois en fin de session
# ══════════════════════════════════════════════════════════════

@CrewBase
class ConsolidationCrew:
    agents_config = "config/consolidation_agents.yaml"
    tasks_config  = "config/consolidation_tasks.yaml"

    @agent
    def session_consolidator(self) -> Agent:
        return Agent(
            config=self.agents_config["session_consolidator"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,          # Analyse + produit un JSON, 2 passes suffisent
            llm=_llm(0.1),
        )

    @agent
    def memory_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["memory_writer"],
            verbose=False,
            allow_delegation=False,
            tools=[UpdateMarkdownTool(), SyncMemoryDbTool()],
            max_iter=6,          # N faits à écrire + 1 sync → N+1 appels tool
            llm=_llm(0.0),
        )

    @task
    def consolidate_task(self) -> Task:
        return Task(config=self.tasks_config["consolidate_task"])

    @task
    def write_task(self) -> Task:
        return Task(config=self.tasks_config["write_task"], context=[self.consolidate_task()])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,       # verbose=True sur la consolidation ralentissait aussi
        )


# ══════════════════════════════════════════════════════════════
# Curiosity Crew — détecte les lacunes contextuelles
# ══════════════════════════════════════════════════════════════

@CrewBase
class CuriosityCrew:
    agents_config = "config/curiosity_agents.yaml"
    tasks_config  = "config/curiosity_tasks.yaml"

    @agent
    def gap_detector(self) -> Agent:
        return Agent(
            config=self.agents_config["gap_detector"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,          # Analyse + produit un JSON, pas de tools
            llm=_llm(0.0),
        )

    @agent
    def questionnaire_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["questionnaire_agent"],
            verbose=False,
            allow_delegation=False,
            tools=[UpdateMarkdownTool(), SyncMemoryDbTool()],
            max_iter=6,
            llm=_llm(0.0),
        )

    @task
    def gap_detection_task(self) -> Task:
        return Task(config=self.tasks_config["gap_detection_task"])

    @task
    def write_answers_task(self) -> Task:
        return Task(config=self.tasks_config["write_answers_task"], context=[self.gap_detection_task()])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

# ══════════════════════════════════════════════════════════════
# Phase 3 — Crews d'action (stubs — seront remplis par étape)
# ══════════════════════════════════════════════════════════════

@CrewBase
class ShellCrew:
    """
    Crew pour l'exécution de commandes système.
    La commande a déjà été validée et confirmée par l'utilisateur dans main.py.
    Cet agent l'exécute et interprète le résultat — sans accès à la mémoire.
    """
    agents_config = "config/shell_agents.yaml"
    tasks_config  = "config/shell_tasks.yaml"

    @agent
    def shell_executor(self) -> Agent:
        from Mnemo.tools.shell_tools import ShellExecuteTool, ReadPdfTool, FileWriterTool
        return Agent(
            config=self.agents_config["shell_executor"],
            tools=[ShellExecuteTool(), ReadPdfTool(), FileWriterTool()],
            verbose=False,
            allow_delegation=False,
            max_iter=5,
            llm=_llm(0.0),
        )

    @task
    def execute_shell_task(self) -> Task:
        return Task(config=self.tasks_config["execute_shell_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def run(self, inputs: dict) -> str:
        result = self.crew().kickoff(inputs=inputs)
        return result.raw.strip()



@CrewBase
class BriefingCrew:
    """
    Crew pour la génération du briefing matinal.
    Produit un fichier briefing.md dans /data depuis :
      - Le calendrier du jour
      - La dernière session consolidée
      - Les points clés de memory.md
    """
    agents_config = "config/briefing_agents.yaml"
    tasks_config  = "config/briefing_tasks.yaml"

    @agent
    def briefing_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["briefing_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.3),
        )

    @task
    def briefing_task(self) -> Task:
        return Task(config=self.tasks_config["briefing_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

@CrewBase
class CalendarWriteCrew:
    """
    Crew pour l'écriture du calendrier ICS local : créer, modifier, supprimer des événements.
    Reçoit : user_message, temporal_context, calendar_context (événements avec UIDs).
    Garanties :
      - Fichiers ICS locaux uniquement (URL distantes refusées).
      - Confirmation obligatoire avant toute opération destructive (update / delete).
      - Opération figée après kickoff : le LLM ne peut pas modifier la commande après confirmation.
    """
    agents_config = "config/calendar_write_agents.yaml"
    tasks_config  = "config/calendar_write_tasks.yaml"

    @agent
    def calendar_writer_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["calendar_writer_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.0),
        )

    @task
    def calendar_write_task(self) -> Task:
        return Task(config=self.tasks_config["calendar_write_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def run(self, inputs: dict) -> str:
        """
        Point d'entrée depuis _route_message.

        Flux :
          1. Vérifie que le calendrier est writable (local).
          2. Enrichit calendar_context avec les UIDs pour le ciblage.
          3. Kickoff LLM → JSON {action, event, target_uid, confirmation_message}.
          4. Pour update/delete : demande confirmation explicite à l'utilisateur.
          5. Exécute l'opération, retourne la confirmation.
        """
        import json as _json
        import re as _re
        from Mnemo.tools.calendar_tools import (
            calendar_is_writable,
            get_events_with_uid,
            format_events_with_uid,
            get_week_dates_for_prompt,
            add_event,
            update_event,
            delete_event,
        )

        if not calendar_is_writable():
            return (
                "Le calendrier est en lecture seule (URL distante) ou non configuré. "
                "Configure CALENDAR_SOURCE avec un chemin de fichier ICS local "
                "pour activer la création et la modification d'événements."
            )

        # Enrichit le contexte avec les UIDs pour que le LLM puisse cibler des événements
        events = get_events_with_uid(days=60)
        cal_ctx = format_events_with_uid(events) if events else "Aucun événement dans les 60 prochains jours."

        from datetime import date as _date
        result = self.crew().kickoff(inputs={
            **inputs,
            "calendar_context": cal_ctx,
            "today_iso": _date.today().isoformat(),
            "week_dates": get_week_dates_for_prompt(),
        })

        raw = result.raw.strip()
        raw = _re.sub(r"^```[a-zA-Z]*\n", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"^```\s*$",        "", raw, flags=_re.MULTILINE)
        raw = raw.strip()

        try:
            plan = _json.loads(raw)
        except Exception:
            return "Je n'ai pas pu interpréter la demande de modification d'agenda. Peux-tu reformuler ?"

        action       = plan.get("action", "create")
        event_fields = plan.get("event") or {}
        target_uid   = plan.get("target_uid") or ""
        confirmation = plan.get("confirmation_message", "")
        web_mode     = inputs.get("_web_mode", False)

        # Résoudre l'index numérique #N → UID complet
        if target_uid and _re.match(r'^#\d+$', target_uid):
            idx = int(target_uid[1:])
            if 0 <= idx < len(events):
                target_uid = events[idx]["uid"]
            else:
                return f"Événement introuvable : index {target_uid} hors limites ({len(events)} événements)."

        # Confirmation obligatoire pour les opérations destructives (CLI uniquement)
        if action in ("update", "delete") and not web_mode:
            target = next((e for e in events if e.get("uid") == target_uid), None)
            print()
            print(f"  📅 Modification agenda — {action.upper()}")
            if target:
                time_str = f" à {target['datetime'].strftime('%H:%M')}" if target.get("datetime") else ""
                print(f"     Événement : {target['title']} ({target['date']}{time_str})")
            elif target_uid:
                print(f"     UID cible  : {target_uid[:24]}…")
            if action == "delete":
                print("     ⚠️  Cet événement sera supprimé définitivement.")
            else:
                print(f"     Modifications : {event_fields}")
            print("     Tape 'oui' pour confirmer (toute autre réponse annule).")
            try:
                answer = input("     Confirmer ? > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("oui", "o", "yes", "y"):
                return "Modification annulée."

        try:
            if action == "create":
                date_iso = event_fields.get("date") or ""
                if not date_iso:
                    return "Impossible de créer l'événement : date manquante."
                add_event(
                    title            = event_fields.get("title", "Événement"),
                    date_iso         = date_iso,
                    time_str         = event_fields.get("time"),
                    duration_minutes = int(event_fields.get("duration_minutes") or 60),
                    location         = event_fields.get("location"),
                    description      = event_fields.get("description"),
                )
                return confirmation or f"Événement '{event_fields.get('title')}' ajouté."

            elif action == "delete":
                if not target_uid:
                    return "Impossible de supprimer : identifiant d'événement manquant."
                ok = delete_event(target_uid)
                if ok:
                    return confirmation or "Événement supprimé."
                return "Événement introuvable dans le calendrier (UID inconnu)."

            elif action == "update":
                if not target_uid:
                    return "Impossible de modifier : identifiant d'événement manquant."
                ok = update_event(target_uid, **event_fields)
                if ok:
                    return confirmation or "Événement modifié."
                return "Événement introuvable dans le calendrier (UID inconnu)."

            else:
                return f"Action inconnue : {action!r}. Actions valides : create, update, delete."

        except Exception as e:
            return f"Erreur lors de la modification du calendrier : {e}"


@CrewBase
class NoteWriterCrew:
    """
    Crew pour l'écriture directe en mémoire longue durée (memory.md).
    Déclenché par route=note : l'utilisateur veut noter quelque chose maintenant,
    sans attendre la consolidation de fin de session.
    Réutilise UpdateMarkdownTool + SyncMemoryDbTool — pas de subprocess, pas de confirmation.
    """
    agents_config = "config/note_agents.yaml"
    tasks_config  = "config/note_tasks.yaml"

    @agent
    def note_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["note_writer"],
            verbose=False,
            allow_delegation=False,
            tools=[UpdateMarkdownTool(), SyncMemoryDbTool()],
            max_iter=4,   # update × N sections + 1 sync
            llm=_llm(0.0),
        )

    @task
    def write_note_task(self) -> Task:
        return Task(config=self.tasks_config["write_note_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def run(self, inputs: dict) -> str:
        from Mnemo.tools.memory_classifier import classify_content
        from Mnemo.tools.ingest_tools import ingest_text_block

        user_message = inputs.get("user_message", "")
        classification = classify_content(user_message)

        print(
            f"[NOTE] classifier={classification.method} "
            f"bucket={classification.bucket} "
            f"conf={classification.confidence:.2f} — {classification.reason}"
        )

        if classification.bucket == "B":
            res = ingest_text_block(user_message)
            if res["status"] == "ingested":
                return (
                    f"Contenu ingéré comme document de référence "
                    f"({res['chunks']} chunks indexés)."
                )
            if res["status"] == "already_ingested":
                return "Ce contenu est déjà dans la base de connaissances."
            return "Le contenu était vide, rien n'a été ingéré."

        # Bucket A — pipeline note courte : memory.md
        result = self.crew().kickoff(inputs=inputs)
        return result.raw.strip()


@CrewBase
class SchedulerCrew:
    """
    Crew pour la planification de tâches différées ou récurrentes.
    Transforme les demandes en langage naturel en entrées scheduled_tasks.
    Reçoit : user_message, temporal_context, evaluation_result.
    """
    agents_config = "config/scheduler_agents.yaml"
    tasks_config  = "config/scheduler_tasks_config.yaml"

    @agent
    def scheduler_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["scheduler_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.0),
        )

    @task
    def schedule_task(self) -> Task:
        return Task(config=self.tasks_config["schedule_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def run(self, inputs: dict) -> str:
        """
        Point d'entrée depuis _route_message.
        Parse le JSON produit par l'agent, crée/annule la tâche en DB,
        retourne la confirmation en langage naturel.
        """
        import json as _json
        import hashlib as _hashlib

        # Assure que la table scheduled_tasks existe (migrate idempotent)
        try:
            from Mnemo.init_db import migrate_db as _migrate
            _migrate()
        except Exception:
            pass

        # Récupère les tâches existantes pour le contexte
        try:
            from Mnemo.tools.scheduler_tasks import list_tasks, create_task, cancel_task
            existing = list_tasks(status="pending")
            existing_tasks = _json.dumps(
                [{"id": t["id"], "action": t["action"],
                  "next_run": t["next_run"], "payload": t["payload"]}
                 for t in existing],
                ensure_ascii=False, indent=2
            )
        except Exception:
            existing_tasks = "[]"
            existing = []

        result = self.crew().kickoff(inputs={
            **inputs,
            "existing_tasks": existing_tasks,
        })

        # Parse le JSON de l'agent
        raw = result.raw.strip()
        # Nettoie les fences markdown éventuelles
        import re as _re
        raw = _re.sub(r'^```[a-zA-Z]*\n', '', raw, flags=_re.MULTILINE)
        raw = _re.sub(r'^```\s*$', '', raw, flags=_re.MULTILINE)
        raw = raw.strip()

        try:
            plan = _json.loads(raw)
        except Exception:
            return "Je n'ai pas pu interpréter la demande de planification. Peux-tu reformuler ?"

        tasks = plan.get("tasks", [])
        if not tasks:
            return "Aucune tâche à planifier trouvée dans la réponse."

        confirmation = plan.get("confirmation_message", "")
        errors = []

        for item in tasks:
            action = item.get("action", "create")

            if action == "cancel":
                tid = item.get("task_id_to_cancel")
                if not tid:
                    errors.append("Annulation sans identifiant de tâche.")
                    continue
                try:
                    cancelled = cancel_task(tid)
                    if not cancelled:
                        errors.append(f"Tâche introuvable ou déjà terminée : {tid}")
                except Exception as e:
                    errors.append(f"Erreur annulation {tid} : {e}")
                continue

            # Création
            task_type   = item.get("task_type", "one_shot")
            task_action = item.get("task_action", "reminder")
            trigger_at  = item.get("trigger_at")
            cron_expr   = item.get("cron_expr")
            payload     = item.get("payload", {})

            seed    = f"{task_action}-{trigger_at or cron_expr}-{payload.get('message','')}"
            task_id = "usr_" + _hashlib.md5(seed.encode()).hexdigest()[:8]

            try:
                created = create_task(
                    task_id    = task_id,
                    task_type  = task_type,
                    action     = task_action,
                    payload    = payload,
                    trigger_at = trigger_at,
                    cron_expr  = cron_expr,
                )
                if not confirmation:
                    confirmation = f"Tâche planifiée pour {created.get('next_run', '?')}."
            except Exception as e:
                errors.append(f"Erreur création ({task_action}) : {e}")

        if errors:
            suffix = " | Erreurs : " + " ; ".join(errors)
            return (confirmation or "Planification partielle.") + suffix

        return confirmation or "Tâches planifiées."


# ══════════════════════════════════════════════════════════════
# ReconnaissanceCrew — Phase 6 : exploration code pré-planification
# ══════════════════════════════════════════════════════════════

@CrewBase
class ReconnaissanceCrew:
    """
    Crew de reconnaissance : lit le code source pertinent et produit
    un recon_context structuré pour PlannerCrew.

    La lecture des fichiers est faite côté Python (pas de LLM).
    Le LLM ne reçoit que les contenus déjà chargés — il ne peut pas halluciner
    des fichiers qu'il n'a pas lus.

    Flux :
      1. Python résout les hints (noms de modules → chemins de fichiers)
      2. Python lit les fichiers et tronque si nécessaire
      3. LLM synthétise → JSON {files_read, symbols_found, summary, ...}
      4. recon_context stocké dans world_state.json["recon_context"]
    """
    agents_config = "config/recon_agents.yaml"
    tasks_config  = "config/recon_tasks.yaml"

    # Taille max du contenu d'un fichier transmis au LLM (en caractères)
    _MAX_FILE_CHARS = 3000

    @agent
    def recon_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["recon_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.0),
        )

    @task
    def recon_task(self) -> Task:
        return Task(config=self.tasks_config["recon_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    @staticmethod
    def _resolve_hints(hints: list[str]) -> list[str]:
        """
        Convertit des noms de modules/fichiers en chemins réels.
        Cherche dans src/ et tests/ à partir du répertoire courant.
        """
        import glob as _glob
        resolved = []
        for hint in hints:
            # Chemin direct
            from pathlib import Path as _Path
            p = _Path(hint)
            if p.exists():
                resolved.append(str(p))
                continue
            # Recherche par nom de fichier
            name = _Path(hint).name
            if not name.endswith(".py"):
                name += ".py"
            matches = _glob.glob(f"**/{name}", recursive=True)
            resolved.extend(matches[:2])  # max 2 fichiers par hint
        return list(dict.fromkeys(resolved))  # déduplique en préservant l'ordre

    @staticmethod
    def _load_files(paths: list[str], max_chars: int) -> dict[str, str]:
        """Lit les fichiers et tronque si nécessaire."""
        from pathlib import Path as _Path
        contents = {}
        for path in paths:
            try:
                text = _Path(path).read_text(encoding="utf-8", errors="ignore")
                if len(text) > max_chars:
                    text = text[:max_chars] + f"\n... [tronqué à {max_chars} caractères]"
                contents[path] = text
            except OSError:
                contents[path] = f"(impossible de lire : {path})"
        return contents

    def run(self, inputs: dict) -> dict:
        """
        Exécute la reconnaissance et retourne le recon_context (dict).
        Met aussi à jour world_state.json["recon_context"].

        Args:
            inputs : {"goal": str, "hints": list[str] | str}

        Returns:
            recon_context dict avec files_read, symbols_found, summary, etc.
        """
        import json as _json
        from Mnemo.tools.memory_tools import save_memory_gap_report, load_world_state

        goal  = inputs.get("goal", "")
        hints = inputs.get("hints", [])
        if isinstance(hints, str):
            hints = [h.strip() for h in hints.split(",") if h.strip()]

        # ── Lecture Python (sans LLM) ──────────────────────────────────
        resolved_paths = self._resolve_hints(hints)
        file_contents  = self._load_files(resolved_paths, self._MAX_FILE_CHARS)

        file_contents_str = "\n\n".join(
            f"### {path}\n```python\n{content}\n```"
            for path, content in file_contents.items()
        ) if file_contents else "(non disponible — aucun fichier trouvé)"

        hints_str = ", ".join(hints) if hints else "(aucun hint fourni)"

        # ── Synthèse LLM ───────────────────────────────────────────────
        try:
            result = self.crew().kickoff(inputs={
                "goal":          goal,
                "hints":         hints_str,
                "file_contents": file_contents_str,
            })
            raw   = result.raw.strip() if result.raw else ""
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end <= start:
                raise ValueError("JSON introuvable")
            recon_context = _json.loads(raw[start:end])
        except Exception as e:
            recon_context = {
                "files_read":    resolved_paths,
                "symbols_found": {},
                "existing_tests": [],
                "key_imports":   [],
                "entry_points":  [],
                "todos_stubs":   [],
                "summary":       f"Reconnaissance partielle — erreur LLM : {e}",
            }

        # ── Persistance dans world_state.json ─────────────────────────
        try:
            ws = load_world_state()
            ws["recon_context"] = recon_context
            ws["knows_module"]  = bool(resolved_paths)
            from Mnemo.context import get_data_dir
            import json as _j
            (get_data_dir() / "world_state.json").write_text(
                _j.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

        return recon_context


# ══════════════════════════════════════════════════════════════
# PlannerCrew — Phase 6 : planification persistante (plan.md)
# ══════════════════════════════════════════════════════════════

@CrewBase
class PlannerCrew:
    """
    Crew pour la décomposition d'un goal complexe en plan persistant (plan.md).

    Flux :
      1. Reçoit goal + recon_context (optionnel) + memory_gap_summary + needs_recon
      2. Si needs_recon=True et recon_context vide → insère étape "Explorer le code"
      3. LLM décompose le goal → JSON {title, steps, crew_targets, context_summary}
      4. PlanStore.create() écrit plan.md dans /data/plans/
      5. Retourne la confirmation avec le chemin du plan créé
    """
    agents_config = "config/planner_agents.yaml"
    tasks_config  = "config/planner_tasks.yaml"

    @agent
    def planner_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["planner_agent"],
            verbose=False,
            allow_delegation=False,
            max_iter=2,
            llm=_llm(0.2),
        )

    @task
    def planner_task(self) -> Task:
        return Task(config=self.tasks_config["planner_task"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )

    def run(self, inputs: dict) -> str:
        import json as _json
        from Mnemo.tools.plan_tools import PlanStore
        from Mnemo.tools.memory_tools import load_world_state

        goal         = inputs.get("user_message", "")
        recon_context = inputs.get("recon_context", "(non disponible)")
        needs_recon  = inputs.get("needs_recon", False)

        # Résumé des lacunes mémoire depuis le WorldState
        ws = load_world_state()
        last_report  = ws.get("last_gap_report", {})
        blocking     = last_report.get("blocking_gaps", [])
        gap_summary  = "\n".join(
            f"- [BLOQUANT] {g.get('description', '')}" for g in blocking
        ) if blocking else "(aucune lacune bloquante connue)"

        try:
            result = self.crew().kickoff(inputs={
                "goal":             goal,
                "recon_context":    recon_context,
                "memory_gap_summary": gap_summary,
            })
            raw = result.raw.strip() if result.raw else ""
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start == -1 or end <= start:
                raise ValueError("JSON introuvable dans la réponse du LLM")

            plan_data     = _json.loads(raw[start:end])
            title         = plan_data.get("title", goal[:60])
            steps         = plan_data.get("steps", [])
            crew_targets  = plan_data.get("crew_targets", {})
            ctx_summary   = plan_data.get("context_summary", "")

            if not steps:
                return "Le planificateur n'a pas pu décomposer ce goal. Peux-tu le reformuler ?"

            plan_path = PlanStore.create(
                goal         = goal,
                steps        = steps,
                context      = ctx_summary,
                crew_targets = crew_targets,
            )

            lines = [f"**Plan créé** : `{plan_path.name}`", f"**Goal** : {goal}", ""]
            for i, step in enumerate(steps, 1):
                crew_t = crew_targets.get(step, "")
                suffix = f" _(crew : {crew_t})_" if crew_t else ""
                lines.append(f"{i}. {step}{suffix}")

            return "\n".join(lines)

        except Exception as e:
            return f"Erreur lors de la planification : {e}"