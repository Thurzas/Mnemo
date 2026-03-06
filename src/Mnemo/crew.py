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
            tools=[RetrieveMemoryTool(), GetSessionMemoryTool(), ListDocumentsTool(), GetCalendarTool(), WebSearchTool()],
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

class CalendarWriteCrew:
    """
    Crew pour l'écriture CalDAV (créer, modifier, supprimer des événements).
    STUB — implémenté en phase 3 étape 3 (agenda CRUD).
    Reçoit : user_message, evaluation_result, web_context, temporal_context.
    Garanties : confirmation obligatoire, opération figée, lecture seule sur la mémoire.
    """
    def run(self, inputs: dict) -> str:
        return (
            "[CalendarWriteCrew non encore implémenté] "
            "La gestion de l'agenda en écriture arrive prochainement."
        )


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