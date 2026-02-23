#!/usr/bin/env python
import sys
import json
import uuid
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

from Mnemo.crew import ConversationCrew, ConsolidationCrew
from Mnemo.tools.memory_tools import update_session_memory, load_session_json, SESSIONS_DIR, check_and_sync


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

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "train":
            train()
        elif sys.argv[1] == "replay":
            replay()
        elif sys.argv[1] == "test":
            test()
    else:
        run()