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
    ) -> Path:
        """
        Crée un nouveau plan.md pour le goal donné.

        Args:
            goal         : description du goal (str)
            steps        : liste des étapes (str) dans l'ordre d'exécution
            context      : contexte de planification (recon_context, mémoire...)
            crew_targets : {étape: crew_cible} — optionnel, annoté dans le plan

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
        path = _plans_dir() / f"plan_{plan_id}.md"
        path.write_text(content, encoding="utf-8")
        return path

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
