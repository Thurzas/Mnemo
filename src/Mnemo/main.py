#!/usr/bin/env python
import sys
import json
import uuid
import warnings
from pathlib import Path
from datetime import datetime

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

from Mnemo.crew import ConversationCrew, ConsolidationCrew
from Mnemo.tools.memory_tools import update_session_memory, load_session_json, SESSIONS_DIR, check_and_sync
from Mnemo.tools.ingest_tools import ingest_file, list_ingested_documents


# ══════════════════════════════════════════════════════════════
# Session helpers
# ══════════════════════════════════════════════════════════════

def new_session_id() -> str:
    return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def handle_message(user_message: str, session_id: str) -> str:
    """Traite un message utilisateur et retourne la réponse de l'agent."""
    result = ConversationCrew().crew().kickoff(inputs={
        "user_message":      user_message,
        "session_id":        session_id,
        "evaluation_result": "",
        "memory_context":    "",
    })
    response = result.raw
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
        try:
            summary = end_session(session_id)
            print(f"✅ Session consolidée :\n{summary}\n")
        except Exception as e:
            print(f"❌ Consolidation échouée : {e}")
            print(f"   Session sauvegardée dans sessions/{session_id}.json")
            print(f"   Elle sera consolidée automatiquement au prochain démarrage.")


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
                print("Usage : python -m Mnemo.main ingest <fichier.pdf>")
            else:
                ingest(sys.argv[2])
        elif sys.argv[1] == "docs":
            list_docs()
        else:
            print(f"Commande inconnue : {sys.argv[1]}")
            print("Commandes disponibles : run, train, replay, test, ingest, docs")
    else:
        run()