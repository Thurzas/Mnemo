from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from waifuclawd.tools.memory_tools import (
    RetrieveMemoryTool,
    GetSessionMemoryTool,
    UpdateMarkdownTool,
    SyncMemoryDbTool,
)


# ══════════════════════════════════════════════════════════════
# Conversation Crew — tourne à chaque message
# ══════════════════════════════════════════════════════════════

@CrewBase
class ConversationCrew:
    """Crew principale : évalue le message, récupère la mémoire, répond."""

    agents_config = "config/conversation_agents.yaml"
    tasks_config  = "config/conversation_tasks.yaml"

    @agent
    def evaluator(self) -> Agent:
        return Agent(
            config=self.agents_config["evaluator"],
            verbose=False,
            allow_delegation=False,
        )

    @agent
    def memory_retriever(self) -> Agent:
        return Agent(
            config=self.agents_config["memory_retriever"],
            verbose=False,
            allow_delegation=False,
            tools=[RetrieveMemoryTool(), GetSessionMemoryTool()],
        )

    @agent
    def main_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["main_agent"],
            verbose=True,
            allow_delegation=False,
        )

    @task
    def evaluate_task(self) -> Task:
        return Task(
            config=self.tasks_config["evaluate_task"],
        )

    @task
    def retrieve_task(self) -> Task:
        return Task(
            config=self.tasks_config["retrieve_task"],
            context=[self.evaluate_task()],
        )

    @task
    def main_task(self) -> Task:
        return Task(
            config=self.tasks_config["main_task"],
            context=[self.retrieve_task()],
        )

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
    """Crew de consolidation : extrait les faits et met à jour la mémoire long terme."""

    agents_config = "config/consolidation_agents.yaml"
    tasks_config  = "config/consolidation_tasks.yaml"

    @agent
    def session_consolidator(self) -> Agent:
        return Agent(
            config=self.agents_config["session_consolidator"],
            verbose=False,
            allow_delegation=False,
        )

    @agent
    def memory_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["memory_writer"],
            verbose=True,
            allow_delegation=False,
            tools=[UpdateMarkdownTool(), SyncMemoryDbTool()],
        )

    @task
    def consolidate_task(self) -> Task:
        return Task(
            config=self.tasks_config["consolidate_task"],
        )

    @task
    def write_task(self) -> Task:
        return Task(
            config=self.tasks_config["write_task"],
            context=[self.consolidate_task()],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )