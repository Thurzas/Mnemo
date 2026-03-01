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

MODEL    = os.getenv("MODEL")
API_BASE = os.getenv("API_BASE")

# ── LLM par rôle ────────────────────────────────────────────────
# Ajuste les modèles selon ce que tu as dans Ollama.
# Le principe : modèle léger pour les tâches structurées, plus lourd pour la réponse finale.
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
            max_iter=5,
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