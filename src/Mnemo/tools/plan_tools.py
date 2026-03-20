"""
Phase 6 — PlanStore : lecture/écriture des plans persistants (plan.md).

Un plan est un fichier Markdown structuré stocké dans /data/plans/.
Il sert à la fois de WorldState persistant et de trace humainement lisible.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from Mnemo.context import get_data_dir


# ── Statuts plan ──────────────────────────────────────────────
STATUS_IN_PROGRESS = "⏳ en cours"
STATUS_DONE        = "✅ terminé"
STATUS_BLOCKED     = "❌ bloqué"

# Regex pour matcher une étape non faite / faite
_RE_STEP_TODO = re.compile(r"^- \[ \] (.+)$")
_RE_STEP_DONE = re.compile(r"^- \[x\] (.+?)(?:\s*✅.*)?$")


def _plans_dir() -> Path:
    d = get_data_dir() / "plans"
    d.mkdir(exist_ok=True, parents=True)
    return d


def _goal_hash(goal: str) -> str:
    return hashlib.md5(goal.encode()).hexdigest()[:8]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _section_bounds(lines: list[str], header: str) -> tuple[int, int]:
    """
    Retourne (start, end) de la section délimitée par `## header`.
    start = index de la ligne après le header
    end   = index de la prochaine section ## (ou fin de fichier)
    """
    start = -1
    for i, line in enumerate(lines):
        if line.strip() == f"## {header}":
            start = i + 1
            break
    if start == -1:
        return (-1, -1)
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## ") and i != start - 1:
            end = i
            break
    return (start, end)


class PlanStore:
    """
    Interface Python pour lire et écrire les plans persistants.
    Tous les chemins sont relatifs à get_data_dir()/plans/.
    """

    # ── Création ──────────────────────────────────────────────

    @staticmethod
    def create(
        goal: str,
        steps: list[str],
        context: str = "",
        crew_targets: dict[str, str] | None = None,
        path: Path | None = None,
    ) -> Path:
        """
        Crée un nouveau plan.md pour le goal donné.

        Args:
            goal         : description du goal (str)
            steps        : liste des étapes (str) dans l'ordre d'exécution
            context      : contexte de planification (recon_context, mémoire...)
            crew_targets : {étape: crew_cible} — optionnel, annoté dans le plan
            path         : chemin explicite où écrire le plan (sinon plans/plan_<id>.md)

        Returns:
            Path du plan créé.
        """
        plan_id   = _goal_hash(goal)
        now       = _now_iso()
        targets   = crew_targets or {}

        steps_md = "\n".join(
            f"- [ ] {step}"
            + (f" — crew : {targets[step]}" if step in targets else "")
            for step in steps
        )

        content = f"""\
# Plan : {goal}

**Créé le** : {now}
**ID** : {plan_id}
**Goal** : {goal}
**Statut** : {STATUS_IN_PROGRESS}

---

## Contexte
{context or "(aucun contexte fourni)"}

## Étapes
{steps_md}

## Bloquants
(aucun)

## Journal
- {now} — Plan créé
"""
        dest = path if path is not None else _plans_dir() / f"plan_{plan_id}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return dest

    # ── Lecture ───────────────────────────────────────────────

    @staticmethod
    def get_active() -> list[Path]:
        """Retourne tous les plans dont le statut est ⏳ en cours."""
        active = []
        for p in sorted(_plans_dir().glob("plan_*.md")):
            text = p.read_text(encoding="utf-8")
            if STATUS_IN_PROGRESS in text:
                active.append(p)
        return active

    @staticmethod
    def get_next_step(plan: Path) -> str | None:
        """Retourne le texte de la première étape [ ] non faite, ou None."""
        lines = plan.read_text(encoding="utf-8").splitlines()
        start, end = _section_bounds(lines, "Étapes")
        if start == -1:
            return None
        for line in lines[start:end]:
            m = _RE_STEP_TODO.match(line.strip())
            if m:
                return m.group(1)
        return None

    @staticmethod
    def list_steps(plan: Path) -> list[dict]:
        """
        Retourne toutes les étapes avec leur statut.
        Chaque entrée : {"text": str, "done": bool}
        """
        lines = plan.read_text(encoding="utf-8").splitlines()
        start, end = _section_bounds(lines, "Étapes")
        if start == -1:
            return []
        steps = []
        for line in lines[start:end]:
            stripped = line.strip()
            if _RE_STEP_TODO.match(stripped):
                steps.append({"text": _RE_STEP_TODO.match(stripped).group(1), "done": False})
            elif _RE_STEP_DONE.match(stripped):
                steps.append({"text": _RE_STEP_DONE.match(stripped).group(1), "done": True})
        return steps

    @staticmethod
    def is_complete(plan: Path) -> bool:
        """True si toutes les étapes sont [x]."""
        return PlanStore.get_next_step(plan) is None

    @staticmethod
    def get_status(plan: Path) -> str:
        """Retourne le statut actuel du plan."""
        text = plan.read_text(encoding="utf-8")
        for status in (STATUS_IN_PROGRESS, STATUS_DONE, STATUS_BLOCKED):
            if status in text:
                return status
        return STATUS_IN_PROGRESS

    # ── Écriture ──────────────────────────────────────────────

    @staticmethod
    def mark_done(plan: Path, step: str) -> None:
        """
        Marque une étape comme terminée.
        Recherche la première ligne `- [ ] {step}` et la remplace par `- [x] ...`.
        Si toutes les étapes sont faites après, met le statut à ✅ terminé.
        """
        lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
        now   = _now_iso()
        found = False
        for i, line in enumerate(lines):
            if _RE_STEP_TODO.match(line.strip()) and step in line:
                indent = len(line) - len(line.lstrip())
                lines[i] = " " * indent + f"- [x] {step} ✅ {now}\n"
                found = True
                break
        if not found:
            return
        plan.write_text("".join(lines), encoding="utf-8")
        if PlanStore.is_complete(plan):
            PlanStore._set_status(plan, STATUS_DONE)
        PlanStore.append_log(plan, f"Étape terminée : {step}")

    @staticmethod
    def mark_failed(plan: Path, step: str, reason: str) -> None:
        """Marque une étape comme échouée [!] (skippée, pas bloquante)."""
        lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
        now   = _now_iso()
        for i, line in enumerate(lines):
            if _RE_STEP_TODO.match(line.strip()) and step in line:
                indent = len(line) - len(line.lstrip())
                lines[i] = " " * indent + f"- [!] {step} ⚠ {now}\n"
                break
        plan.write_text("".join(lines), encoding="utf-8")
        PlanStore.append_log(plan, f"Étape échouée (skip) : {step} — {reason[:120]}")

    @staticmethod
    def add_blocker(plan: Path, blocker: str) -> None:
        """Ajoute un bloquant dans la section ## Bloquants."""
        lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
        start, end = _section_bounds(
            [l.rstrip("\n") for l in lines], "Bloquants"
        )
        if start == -1:
            return

        # Remplace "(aucun)" si présent
        insert_idx = start
        for i in range(start, end):
            if lines[i].strip() == "(aucun)":
                lines[i] = f"- ⚠ {blocker}\n"
                plan.write_text("".join(lines), encoding="utf-8")
                PlanStore._set_status(plan, STATUS_BLOCKED)
                PlanStore.append_log(plan, f"Bloquant ajouté : {blocker}")
                return
            insert_idx = i + 1

        lines.insert(insert_idx, f"- ⚠ {blocker}\n")
        plan.write_text("".join(lines), encoding="utf-8")
        PlanStore._set_status(plan, STATUS_BLOCKED)
        PlanStore.append_log(plan, f"Bloquant ajouté : {blocker}")

    @staticmethod
    def append_log(plan: Path, entry: str) -> None:
        """Ajoute une entrée datée dans la section ## Journal."""
        lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
        start, end = _section_bounds(
            [l.rstrip("\n") for l in lines], "Journal"
        )
        if start == -1:
            lines.append(f"\n## Journal\n- {_now_iso()} — {entry}\n")
        else:
            lines.insert(end, f"- {_now_iso()} — {entry}\n")
        plan.write_text("".join(lines), encoding="utf-8")

    @staticmethod
    def _set_status(plan: Path, status: str) -> None:
        """Remplace la ligne **Statut** dans l'en-tête du plan."""
        text = plan.read_text(encoding="utf-8")
        for s in (STATUS_IN_PROGRESS, STATUS_DONE, STATUS_BLOCKED):
            if s in text:
                text = text.replace(f"**Statut** : {s}", f"**Statut** : {status}", 1)
                break
        plan.write_text(text, encoding="utf-8")


# ── PlanRunner ────────────────────────────────────────────────

# Regex pour extraire le crew cible annoté dans le plan
# ex: "Lire le fichier — crew : shell" → "shell"
_RE_CREW_TARGET = re.compile(r"—\s*crew\s*:\s*(\w+)", re.IGNORECASE)


def _build_step_executor() -> dict:
    """
    Construit le registre d'exécuteurs d'étapes à la demande (imports différés).
    Chaque exécuteur reçoit (step_text, session_id, base_inputs) → str.
    """
    def _temporal() -> str:
        try:
            from Mnemo.tools.calendar_tools import get_temporal_context
            return get_temporal_context()
        except Exception:
            return ""

    def _run_conversation(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import ConversationCrew
        result = ConversationCrew().crew().kickoff(inputs={
            **inputs,
            "user_message":   step,
            "session_id":     session_id,
            "memory_context": "",
            "temporal_context": inputs.get("temporal_context") or _temporal(),
        })
        return result.raw or ""

    def _run_shell(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import ShellCrew
        return ShellCrew().run({
            **inputs,
            "user_message": step,
            "shell_command": step,
            "temporal_context": inputs.get("temporal_context") or _temporal(),
        })

    def _run_note(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import NoteWriterCrew
        return NoteWriterCrew().run({
            "user_message": step,
            "temporal_context": inputs.get("temporal_context") or _temporal(),
        })

    def _run_scheduler(step: str, session_id: str, inputs: dict) -> str:
        # Dans un plan, "crew : scheduler" signifie souvent "planifier/noter une tâche"
        # → on délègue à NoteWriterCrew plutôt que SchedulerCrew (qui attend une requête agenda)
        from Mnemo.crew import NoteWriterCrew
        return NoteWriterCrew().run({
            "user_message": step,
            "temporal_context": inputs.get("temporal_context") or _temporal(),
        })

    def _run_recon(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import ReconnaissanceCrew
        goal  = inputs.get("goal", step)
        hints = inputs.get("hints", [])
        result = ReconnaissanceCrew().run({"goal": goal, "hints": hints})
        return result.get("summary", "Reconnaissance terminée.")

    def _run_curiosity(step: str, session_id: str, inputs: dict) -> str:
        # Curiosity en mode PlanRunner : pas d'interaction utilisateur directe
        # Retourne une invitation à lancer la session manuelle
        return (
            f"Étape de remplissage mémoire : '{step}'. "
            "Lance une session pour combler ces lacunes avant de continuer."
        )

    return {
        "conversation":    _run_conversation,
        "shell":           _run_shell,
        "note":            _run_note,
        "scheduler":       _run_scheduler,
        "reconnaissance":  _run_recon,
        "curiosity":       _run_curiosity,
    }


class StepExecutionError(Exception):
    """Levée quand une étape échoue et bloque le plan."""


class PlanRunner:
    """
    Exécute un plan.md étape par étape.

    Comportement :
    - Reprend à la première étape [ ] (crash recovery automatique)
    - Marque chaque étape [x] après succès
    - Arrêt au premier bloquant : add_blocker() + status ❌
    - Retourne un résumé de l'exécution

    Option B — _STEP_EXECUTOR dict : pas de couplage avec dispatch() / middleware.
    """

    def __init__(self) -> None:
        self._executors = _build_step_executor()

    @staticmethod
    def _get_crew_target(step_text: str) -> str:
        """Extrait le crew cible depuis l'annotation '— crew : xxx'."""
        m = _RE_CREW_TARGET.search(step_text)
        return m.group(1).lower() if m else "conversation"

    @staticmethod
    def _clean_step(step_text: str) -> str:
        """Retire l'annotation crew de l'affichage."""
        return _RE_CREW_TARGET.sub("", step_text).strip(" —")

    def run(
        self,
        plan: Path,
        session_id: str = "",
        base_inputs: dict | None = None,
        max_steps: int = 0,
    ) -> str:
        """
        Exécute le plan jusqu'à complétion ou premier bloquant.

        Args:
            plan        : chemin du plan.md
            session_id  : session courante (pour les crews qui en ont besoin)
            base_inputs : inputs de base transmis à chaque crew
            max_steps   : nombre maximum d'étapes à exécuter (0 = illimité)

        Returns:
            Résumé de l'exécution (étapes faites, bloquant éventuel).
        """
        inputs   = base_inputs or {}
        executed = 0
        blocked  = False

        while True:
            if max_steps and executed >= max_steps:
                break
            step_raw = PlanStore.get_next_step(plan)
            if step_raw is None:
                break  # toutes les étapes sont faites

            crew_target = self._get_crew_target(step_raw)
            step_clean  = self._clean_step(step_raw)
            executor    = self._executors.get(crew_target, self._executors["conversation"])

            PlanStore.append_log(plan, f"Début étape : {step_clean} (crew : {crew_target})")

            try:
                response = executor(step_raw, session_id, inputs)

                # Détecte un bloquant via la réponse (erreur explicite)
                if response and any(
                    marker in response.lower()
                    for marker in ("erreur", "bloqué", "impossible", "échoué", "failed")
                ):
                    raise StepExecutionError(response[:200])

                PlanStore.mark_done(plan, step_raw)
                executed += 1

            except Exception as e:
                blocker = f"{step_clean} — {e}"
                PlanStore.add_blocker(plan, blocker)
                blocked = True
                break

        status = PlanStore.get_status(plan)
        if blocked:
            return (
                f"Plan arrêté après {executed} étape(s). "
                f"Bloquant enregistré dans `{plan.name}`. "
                f"Statut : {status}"
            )
        if PlanStore.is_complete(plan):
            return (
                f"Plan terminé — {executed} étape(s) complétée(s). "
                f"Statut : {status}"
            )
        return f"{executed} étape(s) exécutée(s). Plan en cours : `{plan.name}`."


def check_active_plans() -> list[Path]:
    """
    Retourne les plans actifs (⏳ en cours).
    Appelé au démarrage de session pour proposer la reprise.
    """
    return PlanStore.get_active()
