#!/usr/bin/env python
import sys
import json
import uuid
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

from waifuclawd.crew import ConversationCrew, ConsolidationCrew, CuriosityCrew
from waifuclawd.tools.memory_tools import (
    update_session_memory, load_session_json, SESSIONS_DIR,
    check_and_sync, MARKDOWN_PATH, get_db, compute_hash,
)
from waifuclawd.tools.ingest_tools import ingest_file, list_ingested_documents


# ══════════════════════════════════════════════════════════════
# CuriosityCrew — menu de questionnement
# ══════════════════════════════════════════════════════════════

MAX_QUESTIONS = 5


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
            })

    return answers


def curiosity_session(session_summary: str) -> None:
    """
    Lance une session de questionnement proactif post-consolidation.
    1. GapDetector analyse memory.md + session → produit les questions
    2. Menu CLI → collecte les réponses utilisateur
    3. QuestionnaireAgent reformule + écrit dans memory.md
    """
    # Lit le contenu de memory.md
    memory_content = MARKDOWN_PATH.read_text(encoding="utf-8", errors="ignore") \
        if MARKDOWN_PATH.exists() else ""

    # Questions déjà refusées
    skipped_ids  = _get_skipped_questions()
    db           = get_db()
    skipped_rows = db.execute(
        "SELECT question FROM curiosity_skipped"
    ).fetchall()
    db.close()
    skipped_text = "\n".join(f"- {r[0]}" for r in skipped_rows) or "Aucune"

    # Phase 1 — GapDetector
    print("\n🔍 Analyse des lacunes mémoire en cours...")
    try:
        result = CuriosityCrew().crew().kickoff(inputs={
            "memory_content":    memory_content[:8000],  # limite pour le contexte
            "session_summary":   session_summary or "Pas de résumé disponible.",
            "skipped_questions": skipped_text,
            "answers_json":      "[]",  # non utilisé dans gap_detection_task
        })
        detection = json.loads(result.raw) if isinstance(result.raw, str) else {}
    except Exception as e:
        print(f"  ⚠️  Analyse impossible : {e}")
        return

    if not detection.get("has_gaps") or not detection.get("questions"):
        print("  ✓ Aucune lacune détectée — mémoire complète pour cette session.")
        return

    questions = detection["questions"][:MAX_QUESTIONS]

    # Filtre les questions déjà skippées
    questions = [
        q for q in questions
        if (q.get("id") or compute_hash(q["question"])) not in skipped_ids
    ]
    if not questions:
        return

    # Phase 2 — Menu CLI
    answers = _collect_answers(questions)
    if not answers:
        print("  (Toutes les questions ont été passées)")
        return

    # Phase 3 — QuestionnaireAgent reformule + écrit
    print("\n✍️  Intégration des réponses dans la mémoire...")
    try:
        CuriosityCrew().crew().kickoff(inputs={
            "memory_content":    memory_content[:8000],
            "session_summary":   session_summary or "",
            "skipped_questions": skipped_text,
            "answers_json":      json.dumps(answers, ensure_ascii=False, indent=2),
        })
        print("  ✅ Réponses intégrées dans memory.md")
    except Exception as e:
        print(f"  ❌ Échec de l'intégration : {e}")




def new_session_id() -> str:
    return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def handle_message(user_message: str, session_id: str) -> str:
    """
    Traite un message utilisateur et retourne la réponse de l'agent.
    Si l'évaluateur signale needs_clarification, pose d'abord une question
    de clarification inline avant de répondre.
    """
    result = ConversationCrew().crew().kickoff(inputs={
        "user_message":      user_message,
        "session_id":        session_id,
        "evaluation_result": "",
        "memory_context":    "",
    })
    response = result.raw

    # Détection inline de needs_clarification
    # Le crew retourne parfois le JSON d'évaluation dans le contexte —
    # on tente de l'extraire pour décider si on doit poser une question
    try:
        # L'évaluateur expose needs_clarification dans son output JSON
        # CrewAI stocke les outputs intermédiaires dans result.tasks_output
        if hasattr(result, "tasks_output") and result.tasks_output:
            eval_raw = result.tasks_output[0].raw if result.tasks_output else ""
            eval_json = json.loads(eval_raw) if eval_raw.strip().startswith("{") else {}
            if eval_json.get("needs_clarification") and eval_json.get("clarification_reason"):
                reason = eval_json["clarification_reason"]
                print(f"\n  🤔 Mnemo a besoin d'une précision : {reason}")
                try:
                    clarif = input("    Toi > ").strip()
                    if clarif:
                        # Relance avec le message enrichi
                        enriched = f"{user_message}\n[Précision : {clarif}]"
                        result2  = ConversationCrew().crew().kickoff(inputs={
                            "user_message":      enriched,
                            "session_id":        session_id,
                            "evaluation_result": "",
                            "memory_context":    "",
                        })
                        response = result2.raw
                        update_session_memory(session_id, user_message, response)
                        return response
                except (EOFError, KeyboardInterrupt):
                    pass  # On continue avec la réponse originale
    except (json.JSONDecodeError, AttributeError, IndexError):
        pass  # Silencieux — le needs_clarification est best-effort

    update_session_memory(session_id, user_message, response)
    return response


def end_session(session_id: str) -> str:
    """Consolide la session terminée en mémoire long terme."""
    session = load_session_json(session_id)
    if not session:
        return "Session vide, rien à consolider."

    result = ConsolidationCrew().crew().kickoff(inputs={
        "session_json":       json.dumps(session, ensure_ascii=False, indent=2),
        "consolidated_facts": "",
    })

    # Marque la session comme consolidée
    (SESSIONS_DIR / f"{session_id}.done").touch()
    return result.raw


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
            summary = end_session(session_id)
            print(f"   ✅ OK — {summary[:80]}...")
        except Exception as e:
            print(f"   ❌ Échec : {e}")
            # On marque quand même comme done pour éviter de boucler indéfiniment
            (SESSIONS_DIR / f"{session_id}.done").touch()
            print(f"   ↳ Session marquée comme traitée pour ne pas bloquer au prochain démarrage.")


# ══════════════════════════════════════════════════════════════
# Entrypoints CrewAI (run / train / replay / test)
# ══════════════════════════════════════════════════════════════

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
    print("Tape 'exit' pour terminer proprement.\n")

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
            session_summary = end_session(session_id)
            print(f"✅ Session consolidée :\n{session_summary}\n")
        except Exception as e:
            print(f"❌ Consolidation échouée : {e}")
            print(f"   Session sauvegardée dans sessions/{session_id}.json")
            print(f"   Elle sera consolidée automatiquement au prochain démarrage.")

        # Questionnement proactif — uniquement si la consolidation a réussi
        if session_summary:
            try:
                curiosity_session(session_summary)
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
    Ou directement : python -m waifuclawd.main ingest fichier.pdf
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


def list_docs() -> None:
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
        if sys.argv[1] == "train":
            train()
        elif sys.argv[1] == "replay":
            replay()
        elif sys.argv[1] == "test":
            test()
        elif sys.argv[1] == "ingest":
            if len(sys.argv) < 3:
                print("Usage : python -m waifuclawd.main ingest <fichier.pdf>")
            else:
                ingest(sys.argv[2])
        elif sys.argv[1] == "docs":
            list_docs()
        else:
            print(f"Commande inconnue : {sys.argv[1]}")
            print("Commandes disponibles : run, train, replay, test, ingest, docs")
    else:
        run()