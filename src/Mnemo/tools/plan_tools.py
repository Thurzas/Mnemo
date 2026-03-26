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
        """Retourne le texte de la première étape [ ] non faite (ni [x] ni [!]), ou None."""
        lines = plan.read_text(encoding="utf-8").splitlines()
        start, end = _section_bounds(lines, "Étapes")
        if start == -1:
            return None
        for line in lines[start:end]:
            stripped = line.strip()
            if re.match(r"^- \[!\]", stripped):
                continue  # étape échouée, on passe
            m = _RE_STEP_TODO.match(stripped)
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
    def replace_step(plan: Path, old_step: str, new_steps: list[str]) -> bool:
        """
        Remplace une étape [ ] ou [!] par une liste de nouvelles étapes [ ].

        Les nouvelles étapes s'insèrent à la position de l'ancienne avec le même indentage.
        Chaque nouvelle étape est marquée avec ⟳ pour indiquer qu'elle est issue d'une
        reformulation — ce marqueur empêche une re-reformulation en boucle.
        Retourne True si l'étape a été trouvée et remplacée.
        """
        lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            stripped = line.strip()
            is_todo   = _RE_STEP_TODO.match(stripped)
            is_failed = re.match(r"^- \[!\]", stripped)
            if (is_todo or is_failed) and old_step in line:
                indent = len(line) - len(line.lstrip())
                # Marque ⟳ : indique que l'étape vient d'une reformulation (pas re-reformulable)
                replacements = [" " * indent + f"- [ ] {s} ⟳\n" for s in new_steps]
                lines[i : i + 1] = replacements
                plan.write_text("".join(lines), encoding="utf-8")
                PlanStore.append_log(
                    plan,
                    f"Reformulation : {old_step!r} → {len(new_steps)} sous-étapes",
                )
                return True
        return False

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

# Pattern pour détecter les sous-étapes génériques (produites par une mauvaise reformulation)
_RE_GENERIC_STEP = re.compile(
    r"^-\s*\[\s*\]\s*(sous-?étape|étape|step|phase|tâche|task)\s*\d",
    re.IGNORECASE,
)


def _purge_generic_steps(plan: Path) -> None:
    """
    Supprime les étapes [ ] avec des noms génériques ('Sous-étape N', 'Step N', etc.)
    encore présentes dans plan.md — typiquement des orphelines laissées par une
    reformulation précédente dont seule la première étape a été reformulée.
    """
    lines = plan.read_text(encoding="utf-8").splitlines(keepends=True)
    cleaned = [l for l in lines if not _RE_GENERIC_STEP.match(l.strip())]
    if len(cleaned) < len(lines):
        plan.write_text("".join(cleaned), encoding="utf-8")
        PlanStore.append_log(plan, "Nettoyage : sous-étapes génériques orphelines supprimées")


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

    def _step_filename(step: str) -> str:
        """Nom de fichier sûr dérivé du label d'étape."""
        clean = _RE_CREW_TARGET.sub("", step).strip(" —")
        return re.sub(r"[^\w\-]", "_", clean[:50]).strip("_")

    # ── Mapping crew_target → action KG ──────────────────────────────
    _CREW_TO_KG_ACTION: dict[str, str] = {
        "shell":          "write_markdown_file",
        "note":           "analyse_et_note",
        "conversation":   "generate_response",
        "scheduler":      "create_structured_content",
        "reconnaissance": "reconnaissance",
        "curiosity":      "assess_memory_gaps",
        "planner":        "spawn_sub_plan",
    }

    def _kg_actions(inputs: dict, step_text: str) -> list[dict]:
        """
        Interroge le HP-KG pour les actions connues de cette étape.
        Retourne une liste de dicts {action_label, weight} triés par poids.
        Retourne [] si KG indisponible ou step inconnu.
        """
        try:
            from Mnemo.context import get_data_dir as _gdd
            from Mnemo.tools.kg_tools import kg_actions_for_step
            db_path  = _gdd() / "memory.db"
            step_clean = _RE_CREW_TARGET.sub("", step_text).strip(" —")
            rows     = kg_actions_for_step(db_path, step_clean)
            return [{"action_label": r["dst_label"], "weight": r.get("weight", 1.0)} for r in rows]
        except Exception:
            return []

    # Mapping langage → extension par défaut
    _LANG_EXT: dict[str, str] = {
        "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts",
        "html": "html", "css": "css", "python": "py", "py": "py",
        "json": "json", "yaml": "yaml", "yml": "yml", "sh": "sh",
        "bash": "sh", "markdown": "md", "md": "md", "text": "txt",
        "sql": "sql", "rust": "rs", "go": "go", "java": "java",
        "c": "c", "cpp": "cpp", "toml": "toml",
    }

    def _extract_code_files(
        response: str,
        existing_files: list[str] | None = None,
    ) -> list[tuple[str, str]]:
        """
        Parse les blocs de code fencés dans une réponse LLM.

        Formats reconnus :
          ```filename.js          ← nom de fichier direct (priorité)
          ```javascript           ← langage seul → fallback sur existing_files ou nom générique

        existing_files : liste des chemins relatifs déjà présents dans src/ (ex: ["src/App.js"]).
        Quand le LLM ne nomme pas son fichier mais qu'un seul fichier de même extension existe,
        on réutilise ce nom au lieu de créer code.js.

        Retourne une liste de (chemin_relatif, contenu).
        """
        files: list[tuple[str, str]] = []
        # Index des existants par extension pour le fallback
        existing_by_ext: dict[str, list[str]] = {}
        for f in (existing_files or []):
            ext = Path(f).suffix.lower()
            existing_by_ext.setdefault(ext, []).append(f)

        unnamed_count: dict[str, int] = {}
        pattern = re.compile(r"```([^\n`]+)\n(.*?)```", re.DOTALL)

        for m in pattern.finditer(response):
            header  = m.group(1).strip()
            content = m.group(2).strip()
            if not content:
                continue

            # Heuristique nom de fichier : contient un "." après un caractère word
            # ex: "app.js", "src/index.html", "components/Foo.tsx" → oui
            # "javascript", "python" → non
            if re.search(r"\w\.\w", header):
                fname = re.sub(r"[^\w\-./]", "_", header.lstrip("/"))
                files.append((fname, content))
            else:
                lang = header.lower().split()[0] if header else "txt"
                ext  = "." + _LANG_EXT.get(lang, "txt")
                # Fallback intelligent : si un seul fichier existant a cette extension, on le réutilise
                candidates = existing_by_ext.get(ext, [])
                idx = unnamed_count.get(ext, 0)
                unnamed_count[ext] = idx + 1
                if candidates and idx < len(candidates):
                    files.append((candidates[idx], content))
                else:
                    # Vraiment pas de candidat — nom générique mais pas "code.js"
                    suffix = f"_{idx}" if idx else ""
                    files.append((f"src/app{suffix}{ext}", content))

        return files

    def _write_to_project_src(inputs: dict, step_label: str, content: str) -> None:
        """
        Écrit le contenu dans projects/<slug>/src/.
        - Si des blocs de code nommés sont détectés → écrit chaque fichier avec sa vraie extension
        - Sinon → fallback Markdown (comportement legacy)
        """
        slug = inputs.get("slug")
        if not slug or not content:
            return
        try:
            from Mnemo.tools.sandbox_tools import write_file as _wf
            code_files = _extract_code_files(content)
            if code_files:
                for rel_path, body in code_files:
                    # Préfixer src/ si pas déjà dans un sous-dossier absolu
                    dest = rel_path if rel_path.startswith("src/") else f"src/{rel_path}"
                    _wf(slug, dest, body, commit_msg=f"agent: {step_label[:50]}")
            else:
                # Fallback : Markdown
                filename = re.sub(r"[^\w\-]", "_", step_label[:40]).strip("_").lower() + ".md"
                _wf(slug, f"src/{filename}", content, commit_msg=f"agent: {step_label[:50]}")
        except Exception:
            pass

    def _save_output(inputs: dict, step: str, content: str) -> None:
        """Écrit le résultat brut d'une étape dans projects/<slug>/outputs/<step>.md."""
        project_dir = inputs.get("project_dir")
        if not project_dir or not content:
            return
        from pathlib import Path as _Path
        out_dir = _Path(project_dir) / "outputs"
        out_dir.mkdir(exist_ok=True)
        (_Path(project_dir) / "outputs" / f"{_step_filename(step)}.md").write_text(
            content, encoding="utf-8"
        )

    def _summarise(content: str, max_chars: int = 600) -> str:
        """
        Extrait les premières lignes non-vides d'un output jusqu'à max_chars.
        Évite d'injecter de longs blobs de texte dans memory.md.
        """
        lines, total = [], 0
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            total += len(stripped) + 1
            lines.append(stripped)
            if total >= max_chars:
                lines.append("…")
                break
        return "\n".join(lines)

    def _update_project_memory(inputs: dict, step_label: str, content: str) -> None:
        """
        Met à jour memory.md du projet avec le résumé de l'étape complétée.

        Structure de memory.md :
          # Mémoire : <goal>
          ## Étapes complétées
          ### <step_label>
          *<date>*
          <résumé>
        """
        project_dir = inputs.get("project_dir")
        if not project_dir or not content:
            return
        from pathlib import Path as _Path
        from datetime import datetime as _dt
        mem_path = _Path(project_dir) / "memory.md"
        goal     = inputs.get("goal", "Projet")
        now      = _dt.now().strftime("%Y-%m-%d %H:%M")
        summary  = _summarise(content)

        # Initialisation si vide ou absent
        if not mem_path.exists() or mem_path.stat().st_size < 10:
            mem_path.write_text(
                f"# Mémoire : {goal}\n\n## Étapes complétées\n",
                encoding="utf-8",
            )

        section = (
            f"\n### {step_label}\n"
            f"*{now}*\n\n"
            f"{summary}\n"
        )
        with mem_path.open("a", encoding="utf-8") as fh:
            fh.write(section)

        # Git commit discret
        try:
            from Mnemo.tools.sandbox_tools import _git_commit as _sgc, _project_path
            slug = inputs.get("slug")
            if slug:
                _sgc(_project_path(slug), f"memory: {step_label[:50]}", ["memory.md"])
        except Exception:
            pass

    def _load_previous_outputs(inputs: dict) -> str:
        """
        Charge le contexte des étapes précédentes depuis memory.md (source de vérité),
        avec fallback sur outputs/ si memory.md est vide.
        """
        project_dir = inputs.get("project_dir")
        if not project_dir:
            return ""
        from pathlib import Path as _Path
        mem_path = _Path(project_dir) / "memory.md"
        if mem_path.exists() and mem_path.stat().st_size > 50:
            try:
                text = mem_path.read_text(encoding="utf-8")
                # Tronqué à 3000 chars — structuré donc dense en signal utile
                if len(text) > 3000:
                    text = text[:3000] + "\n\n[…tronqué]"
                return f"## Mémoire du projet (étapes précédentes)\n\n{text}"
            except Exception:
                pass
        # Fallback : outputs bruts (ancien comportement)
        out_dir = _Path(project_dir) / "outputs"
        if not out_dir.exists():
            return ""
        parts = []
        for f in sorted(out_dir.iterdir()):
            if f.suffix == ".md" and f.is_file():
                try:
                    content = f.read_text(encoding="utf-8")[:1000]
                    parts.append(f"### {f.stem.replace('_', ' ')}\n{content}")
                except Exception:
                    pass
        if not parts:
            return ""
        return "## Résultats des étapes précédentes\n\n" + "\n\n".join(parts)

    def _conversation_inputs(step: str, session_id: str, inputs: dict) -> dict:
        """Inputs minimaux requis par conversation_tasks.yaml, avec contexte pipeline."""
        prev     = _load_previous_outputs(inputs)
        base_mem = inputs.get("memory_context", "")

        # E.1 — Index du projet (carte des fichiers existants)
        project_ctx = ""
        slug = inputs.get("slug")
        if slug:
            try:
                from Mnemo.tools.project_index import format_project_context
                project_ctx = format_project_context(slug)
            except Exception:
                pass

        # E.2 — Passages issus des documents ingérés (RAG)
        doc_ctx = ""
        try:
            from Mnemo.tools.doc_context import search_ingested_docs, format_doc_context
            doc_ctx = format_doc_context(search_ingested_docs(step))
        except Exception:
            pass

        memory_ctx = "\n\n".join(filter(None, [base_mem, prev, project_ctx, doc_ctx]))
        return {
            **inputs,
            "user_message":      step,
            "session_id":        session_id or "plan",
            "memory_context":    memory_ctx,
            "temporal_context":  inputs.get("temporal_context") or _temporal(),
            "calendar_context":  inputs.get("calendar_context", ""),
            "evaluation_result": inputs.get("evaluation_result", (
                '{"route":"conversation","needs_memory":false,'
                '"needs_web":false,"needs_clarification":false}'
            )),
        }

    def _run_conversation(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import ConversationCrew
        result = ConversationCrew().crew().kickoff(inputs=_conversation_inputs(step, session_id, inputs))
        response = result.raw or ""
        _save_output(inputs, step, response)
        return response

    # Vérificateurs syntaxiques par extension : uniquement Python pour l'instant.
    # node --check ne supporte pas JSX (React) → false positives constants.
    # Les fichiers .js/.ts sont écrits mais leur syntaxe n'est pas vérifiée
    # (le LLM devra s'auto-corriger lors des étapes de test dédiées).
    _SYNTAX_CHECKS: dict[str, str] = {
        ".py": "python -m py_compile {path}",
    }

    def _syntax_check_files(slug: str, written_paths: list[str]) -> list[tuple[str, str]]:
        """
        Vérifie la syntaxe des fichiers écrits (Python uniquement).
        Retourne la liste de (path, error_message) pour les fichiers en erreur.
        """
        from Mnemo.tools.sandbox_tools import run_command as _rc
        errors = []
        for rel_path in written_paths:
            ext = Path(rel_path).suffix.lower()
            if ext not in _SYNTAX_CHECKS:
                continue
            cmd = _SYNTAX_CHECKS[ext].format(path=rel_path)
            result = _rc(slug, cmd)
            if result.get("returncode", 0) != 0:
                err = (result.get("stderr") or result.get("stdout") or "erreur inconnue").strip()
                errors.append((rel_path, err[:400]))
        return errors

    # Extensions considérées comme du code source (pas de documentation)
    _CODE_EXTENSIONS = {
        ".js", ".jsx", ".ts", ".tsx", ".py", ".html", ".css", ".scss",
        ".json", ".yaml", ".yml", ".sh", ".rs", ".go", ".java", ".rb",
    }
    # Budget total de lecture src/ injecté dans le prompt (chars)
    _SRC_TOTAL_BUDGET = 4000

    def _read_src_files(slug: str) -> list[tuple[str, str]]:
        """
        Lit les fichiers de src/ du projet et retourne [(rel_path, content), ...].

        Budget total : _SRC_TOTAL_BUDGET chars répartis entre les fichiers.
        Les fichiers les plus petits sont injectés en entier ; les plus grands sont tronqués.
        Les fichiers > 50 Ko sont ignorés.
        """
        if not slug:
            return []
        try:
            from Mnemo.tools.sandbox_tools import list_files as _lf, read_file as _rf
            all_files = _lf(slug)
            src_files = [
                f for f in all_files
                if f.startswith("src/") and not f.endswith("/")
                and Path(f).suffix.lower() in _CODE_EXTENSIONS
            ]
            # Lire tous, ignorer les trop gros
            loaded: list[tuple[str, str]] = []
            for rel in src_files[:12]:
                data = _rf(slug, rel)
                content = data.get("content", "")
                if content and len(content) <= 50_000:
                    loaded.append((rel, content))
            # Répartir le budget : les petits fichiers en entier, les gros tronqués
            loaded.sort(key=lambda x: len(x[1]))  # petits d'abord
            result, used = [], 0
            per_file_max = max(400, _SRC_TOTAL_BUDGET // max(len(loaded), 1))
            for rel, content in loaded:
                if used >= _SRC_TOTAL_BUDGET:
                    break
                allowed = min(per_file_max, _SRC_TOTAL_BUDGET - used)
                if len(content) <= allowed:
                    result.append((rel, content))
                    used += len(content)
                else:
                    result.append((rel, content[:allowed] + "\n…[tronqué]"))
                    used += allowed
            return result
        except Exception:
            return []

    def _run_shell(step: str, session_id: str, inputs: dict) -> str:
        """
        crew : shell dans un plan = produire et écrire des fichiers réels dans src/.

        Le LLM est invité à répondre avec des blocs de code fencés nommés :
          ```app.js
          // code here
          ```
        Les fichiers src/ existants sont injectés dans le prompt pour que le LLM
        puisse les modifier plutôt que créer de nouveaux fichiers.
        Boucle de vérification (B2) : après écriture, check syntaxe Python → 1 retry.
        """
        from Mnemo.crew import ConversationCrew
        clean = _RE_CREW_TARGET.sub("", step).strip(" —").rstrip(" ⟳").strip()
        goal  = inputs.get("goal", "")
        slug  = inputs.get("slug", "")

        # Lire les fichiers src/ existants pour les injecter dans le prompt
        existing_files = _read_src_files(slug)

        def _build_existing_ctx() -> str:
            if not existing_files:
                return ""
            lines = ["## Fichiers existants dans src/ (à modifier si besoin)"]
            for rel, preview in existing_files:
                lines.append(f"\n### {rel}\n```\n{preview}\n```")
            return "\n".join(lines)

        def _build_msg(error_feedback: str = "") -> str:
            existing_ctx = _build_existing_ctx()
            existing_names = [rel for rel, _ in existing_files]

            rules = [
                "- Écris du code réel et fonctionnel, pas de commentaires placeholder",
                "- Utilise l'extension correcte : .js, .jsx, .html, .css, .py, .ts, etc.",
                "- Tu peux produire plusieurs fichiers si nécessaire",
                "- Si c'est de la documentation/analyse, utilise .md",
                "- INTERDIT : nommer un fichier 'code.js', 'code_1.js', 'untitled', 'example', etc.",
            ]
            if existing_names:
                rules.append(
                    f"- PRIORITÉ : modifie les fichiers existants ({', '.join(existing_names)}) "
                    "plutôt que d'en créer de nouveaux. Utilise leur nom EXACT."
                )

            base = (
                f"Tâche : {clean}\n"
                f"Objectif du projet : {goal}\n"
            )
            if existing_ctx:
                base += f"\n{existing_ctx}\n"
            base += (
                "\nProduis le contenu complet de chaque fichier créé ou modifié. "
                "Pour chaque fichier, utilise EXACTEMENT ce format :\n\n"
                "```chemin/vers/fichier.ext\n"
                "// contenu complet ici\n"
                "```\n\n"
                "Règles :\n" + "\n".join(rules)
            )
            if error_feedback:
                base += f"\n\n⚠ ERREURS À CORRIGER :\n{error_feedback}\nCorrige ces erreurs dans ta réponse."
            return base

        written: list[str] = []
        existing_names = [rel for rel, _ in existing_files]

        def _write_and_track(response: str) -> None:
            nonlocal written
            code_files = _extract_code_files(response, existing_files=existing_names)
            if not code_files:
                filename = re.sub(r"[^\w\-]", "_", clean[:40]).strip("_").lower() + ".md"
                code_files = [(f"src/{filename}", response)]
            written = []
            if slug:
                from Mnemo.tools.sandbox_tools import write_file as _wf
                for rel_path, body in code_files:
                    dest = rel_path if rel_path.startswith("src/") else f"src/{rel_path}"
                    _wf(slug, dest, body, commit_msg=f"agent: {clean[:50]}")
                    written.append(dest)
            else:
                # Fallback sans slug
                from pathlib import Path as _Path
                project_dir = inputs.get("project_dir", "")
                if project_dir:
                    src_dir = _Path(project_dir) / "src"
                    src_dir.mkdir(exist_ok=True)
                    for rel_path, body in code_files:
                        fname = Path(rel_path).name
                        (_Path(project_dir) / "src" / fname).write_text(body, encoding="utf-8")

        # Tentative 1
        result1 = ConversationCrew().crew().kickoff(
            inputs=_conversation_inputs(_build_msg(), session_id, inputs)
        )
        response = result1.raw or ""
        _save_output(inputs, step, response)
        _write_and_track(response)

        # Boucle B2 — vérification syntaxe (1 retry max)
        if slug and written:
            errors = _syntax_check_files(slug, written)
            if errors:
                err_lines = "\n".join(f"- {p}: {e}" for p, e in errors)
                result2 = ConversationCrew().crew().kickoff(
                    inputs=_conversation_inputs(_build_msg(err_lines), session_id, inputs)
                )
                response2 = result2.raw or ""
                if response2:
                    _save_output(inputs, step, response2)
                    _write_and_track(response2)
                    # Vérifie à nouveau — si toujours en erreur, on lève pour que PlanRunner marque [!]
                    errors2 = _syntax_check_files(slug, written)
                    if errors2:
                        err_summary = "; ".join(f"{p}" for p, _ in errors2)
                        raise RuntimeError(f"Syntax errors after retry: {err_summary}")
                    response = response2

        return response

    def _run_note(step: str, session_id: str, inputs: dict) -> str:
        """
        crew : note dans un plan = analyse + résultat structuré.
        Consulte le KG — action par défaut : analyse_et_note.
        Résultat sauvegardé en output (memory.md est mis à jour par PlanRunner).
        """
        from Mnemo.crew import ConversationCrew
        clean = _RE_CREW_TARGET.sub("", step).strip(" —")

        actions      = _kg_actions(inputs, step)
        action_label = actions[0]["action_label"] if actions else "analyse_et_note"

        msg    = f"Rédige une analyse détaillée et structurée en markdown pour : {clean}"
        result = ConversationCrew().crew().kickoff(
            inputs=_conversation_inputs(msg, session_id, inputs)
        )
        response = result.raw or ""
        _save_output(inputs, step, response)

        # Pour write_markdown_file (si KG le précise), écrire aussi dans src/
        if action_label == "write_markdown_file":
            _write_to_project_src(inputs, clean, response)

        return response

    def _run_scheduler(step: str, session_id: str, inputs: dict) -> str:
        """crew : scheduler dans un plan = structure/plan détaillé en markdown."""
        from Mnemo.crew import ConversationCrew
        clean = _RE_CREW_TARGET.sub("", step).strip(" —")

        actions      = _kg_actions(inputs, step)
        action_label = actions[0]["action_label"] if actions else "create_structured_content"

        msg    = f"Crée et structure un plan détaillé en markdown pour : {clean}"
        result = ConversationCrew().crew().kickoff(
            inputs=_conversation_inputs(msg, session_id, inputs)
        )
        response = result.raw or ""
        _save_output(inputs, step, response)

        if action_label == "write_markdown_file":
            _write_to_project_src(inputs, clean, response)

        return response

    def _run_recon(step: str, session_id: str, inputs: dict) -> str:
        from Mnemo.crew import ReconnaissanceCrew
        goal   = inputs.get("goal", step)
        hints  = inputs.get("hints", [])
        result = ReconnaissanceCrew().run({"goal": goal, "hints": hints})
        summary = result.get("summary", "Reconnaissance terminée.")
        _save_output(inputs, step, summary)
        return summary

    def _run_curiosity(step: str, session_id: str, inputs: dict) -> str:
        return (
            f"Étape de remplissage mémoire : '{step}'. "
            "Lance une session pour combler ces lacunes avant de continuer."
        )

    def _run_planner(step: str, session_id: str, inputs: dict) -> str:
        """
        crew : planner dans un plan = décompose l'étape en sous-plan et l'exécute.

        Mécanisme HTN (Hierarchical Task Networks) :
          - Appelle ConversationCrew pour décomposer le label d'étape en sous-étapes JSON
          - Crée un sous-plan dans projects/<slug>/sub_plans/<step_slug>/plan.md
          - Lance PlanRunner récursivement sur ce sous-plan
          - Guard : _plan_depth dans inputs (décrément à chaque niveau, stop à 0)
        """
        import json as _json
        from pathlib import Path as _Path

        depth = inputs.get("_plan_depth", 2)
        clean = _RE_CREW_TARGET.sub("", step).strip(" —")
        slug  = inputs.get("slug")

        # Profondeur max atteinte → traitement plat sans récursion
        if depth <= 0:
            return _run_conversation(step, session_id, inputs)

        # Demander à ConversationCrew de décomposer en sous-étapes JSON
        decompose_msg = (
            "Décompose la tâche suivante en 3 à 6 sous-étapes concrètes et ordonnées. "
            "Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après. "
            'Format : {"steps": ["étape 1", "étape 2"], '
            '"crew_targets": {"étape 1": "shell", "étape 2": "note"}}\n\n'
            "Tâche : " + clean
        )
        sub_data: dict = {}
        try:
            from Mnemo.crew import ConversationCrew as _CC
            res = _CC().crew().kickoff(
                inputs=_conversation_inputs(decompose_msg, session_id, inputs)
            )
            raw   = res.raw or ""
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start != -1 and end > start:
                sub_data = _json.loads(raw[start:end])
        except Exception:
            pass

        sub_steps   = sub_data.get("steps", [])
        sub_targets = sub_data.get("crew_targets", {})

        # Décomposition impossible → traitement plat
        if not sub_steps:
            return _run_conversation(step, session_id, inputs)

        # Nom du dossier sous-plan
        sub_slug = re.sub(r"[^\w\-]", "_", clean[:30]).strip("_").lower()

        # Créer le sous-plan dans projects/<slug>/sub_plans/<step_slug>/
        if slug:
            from Mnemo.context import get_data_dir as _gdd
            sub_plan_dir  = _gdd() / "projects" / slug / "sub_plans" / sub_slug
            sub_plan_dir.mkdir(parents=True, exist_ok=True)
            sub_plan_path = PlanStore.create(
                goal         = clean,
                steps        = sub_steps,
                crew_targets = sub_targets,
                path         = sub_plan_dir / "plan.md",
            )
            sub_project_dir = str(sub_plan_dir)
        else:
            sub_plan_path   = PlanStore.create(
                goal         = clean,
                steps        = sub_steps,
                crew_targets = sub_targets,
            )
            sub_project_dir = inputs.get("project_dir", "")

        sub_inputs = {
            **inputs,
            "project_dir":  sub_project_dir,
            "goal":         clean,
            "_plan_depth":  depth - 1,
        }

        sub_runner = PlanRunner()
        summary    = sub_runner.run(
            sub_plan_path,
            session_id  = session_id,
            base_inputs = sub_inputs,
        )
        _save_output(inputs, step, summary)
        return summary

    return {
        "conversation":      _run_conversation,
        "shell":             _run_shell,
        "note":              _run_note,
        "scheduler":         _run_scheduler,
        "reconnaissance":    _run_recon,
        "curiosity":         _run_curiosity,
        "planner":           _run_planner,
        # Callable utilitaire exposé pour PlanRunner (pas un crew_target)
        "__update_memory__": _update_project_memory,
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
        """Retire l'annotation crew et le marqueur ⟳ de reformulation."""
        return _RE_CREW_TARGET.sub("", step_text).strip(" —").rstrip(" ⟳").strip()

    def _try_reformulate(
        self,
        plan: Path,
        step_raw: str,
        reason: str,
        session_id: str,
        inputs: dict,
    ) -> bool:
        """
        Tente de reformuler une étape bloquée en sous-étapes plus concrètes.

        Appelle ConversationCrew avec un prompt de décomposition, parse le JSON retourné,
        puis appelle PlanStore.replace_step() pour réécrire plan.md.

        Retourne True si la reformulation a réussi (plan modifié).
        Retourne False si le LLM n'a pas pu produire de JSON valide.
        """
        import json as _json
        from Mnemo.crew import ConversationCrew

        # ⟳ = étape déjà issue d'une reformulation → pas de re-reformulation
        if "⟳" in step_raw:
            return False

        step_clean  = self._clean_step(step_raw)
        crew_target = self._get_crew_target(step_raw)

        goal = inputs.get("goal", "")
        prompt = (
            f"L'étape suivante d'un plan de projet est bloquée ou trop vague :\n"
            f"Étape : {step_clean}\n"
            f"Crew prévu : {crew_target}\n"
            f"Problème : {reason}\n"
            f"Objectif du projet : {goal}\n\n"
            "Décompose cette étape en 2 à 4 sous-étapes CONCRÈTES avec des noms descriptifs.\n\n"
            "RÈGLES ABSOLUES :\n"
            "- Chaque nom décrit l'action réelle (ex: 'Créer src/App.jsx', "
            "'Analyser les dépendances npm', 'Explorer les fichiers src/')\n"
            "- INTERDIT : 'Sous-étape N', 'Étape N', 'Step N', 'Phase N', 'Tâche N'\n"
            "- Chaque étape se termine par '— crew : <cible>'\n\n"
            "Réponds UNIQUEMENT avec ce JSON, sans texte avant ni après :\n"
            '{"steps": ["<action concrète> — crew : shell", "<autre action> — crew : note"]}\n\n'
            "RÈGLES crew :\n"
            "- créer / écrire / implémenter / générer / développer → crew : shell\n"
            "- analyser / documenter / résumer / évaluer → crew : note\n"
            "- rechercher / explorer / scanner / lister → crew : reconnaissance\n"
            "- définir / planifier / organiser / discuter → crew : conversation"
        )

        try:
            result = ConversationCrew().crew().kickoff(inputs={
                **inputs,
                "user_message":      prompt,
                "session_id":        session_id or "plan",
                "memory_context":    "",
                "temporal_context":  "",
                "calendar_context":  "",
                "evaluation_result": (
                    '{"route":"conversation","needs_memory":false,'
                    '"needs_web":false,"needs_clarification":false}'
                ),
            })
            raw = result.raw or ""
            # Extraction robuste : parcourt tous les { } et prend le premier contenant "steps"
            import json as _json
            data = None
            for m in re.finditer(r'\{', raw):
                start = m.start()
                depth, end = 0, start
                for j, ch in enumerate(raw[start:]):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = start + j + 1
                            break
                candidate = raw[start:end]
                if '"steps"' in candidate:
                    try:
                        data = _json.loads(candidate)
                        break
                    except Exception:
                        continue
            if data is None:
                return False

            _generic = re.compile(
                r'^(sous-?étape|étape|step|phase|tâche|task)\s*\d', re.IGNORECASE
            )
            new_steps_raw = [s.strip() for s in data.get("steps", []) if s.strip()]
            # Filtrer noms génériques et dédupliquer
            seen: set[str] = set()
            new_steps = []
            for s in new_steps_raw:
                label = re.sub(r"\s*—\s*crew\s*:.*", "", s).strip()
                if not _generic.match(label) and s not in seen:
                    new_steps.append(s)
                    seen.add(s)

            if len(new_steps) < 2:
                return False

            ok = PlanStore.replace_step(plan, step_raw, new_steps)
            if ok:
                # Nettoyer les sous-étapes génériques orphelines (sœurs de la même
                # reformulation qui n'ont pas encore été exécutées)
                _purge_generic_steps(plan)
            return ok
        except Exception:
            return False

    @staticmethod
    def _kg_feedback(inputs: dict, step_label: str, crew_target: str, success: bool) -> None:
        """
        Renforce (+0.1) ou affaiblit (-0.05) l'arête (step)-[requires]->(action) dans le KG.
        Appelé après chaque étape pour que le KG apprenne quelles actions fonctionnent.
        """
        try:
            from Mnemo.context import get_data_dir
            from Mnemo.tools.kg_tools import kg_actions_for_step, kg_reinforce_edge
            db_path = get_data_dir() / "memory.db"
            actions = kg_actions_for_step(db_path, step_label)
            if not actions:
                return
            delta = +0.1 if success else -0.05
            for row in actions:
                kg_reinforce_edge(
                    db_path,
                    src_id  = row["src"],
                    rel     = "requires",
                    dst_id  = row["dst"],
                    delta   = delta,
                    session_id = inputs.get("session_id", "plan"),
                    outcome = "success" if success else "failed",
                )
        except Exception:
            pass

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
        inputs = dict(base_inputs or {})
        # Injecter _plan_depth par défaut (max 2 niveaux de récursion)
        if "_plan_depth" not in inputs:
            inputs["_plan_depth"] = 2
        executed    = 0
        skipped     = 0
        reformulated: set[str] = set()  # guard : pas de reformulation en boucle

        while True:
            if max_steps and (executed + skipped) >= max_steps:
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
                PlanStore.mark_done(plan, step_raw)
                try:
                    self._executors["__update_memory__"](inputs, step_clean, response)
                except Exception:
                    pass  # mise à jour mémoire non-bloquante
                self._kg_feedback(inputs, step_clean, crew_target, success=True)
                executed += 1

            except Exception as e:
                reason = str(e)[:200]
                # Tentative de reformulation (une seule fois par étape)
                if step_raw not in reformulated:
                    ok = self._try_reformulate(plan, step_raw, reason, session_id, inputs)
                    if ok:
                        reformulated.add(step_raw)
                        # La boucle reprend sur la première sous-étape reformulée
                        continue
                PlanStore.mark_failed(plan, step_raw, reason)
                self._kg_feedback(inputs, step_clean, crew_target, success=False)
                skipped += 1
                # On continue vers la prochaine étape

        status = PlanStore.get_status(plan)
        skip_note = f", {skipped} ignorée(s)" if skipped else ""
        if PlanStore.is_complete(plan):
            return f"Plan terminé — {executed} étape(s) complétée(s){skip_note}. Statut : {status}"
        return f"{executed} étape(s) exécutée(s){skip_note}. Plan en cours : `{plan.name}`."


def check_active_plans() -> list[Path]:
    """
    Retourne les plans actifs (⏳ en cours).
    Appelé au démarrage de session pour proposer la reprise.
    """
    return PlanStore.get_active()
