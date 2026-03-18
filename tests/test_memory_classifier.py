"""
test_memory_classifier.py — Tests du classifier mémoire (Phase 4)

Niveau 1 (N1) : heuristique pure — zéro LLM, zéro réseau
  - Bucket A : faits courts, pronoms personnels, intent markers A
  - Bucket B : contenu structuré, taille > seuil, intent markers B
  - Confiance et méthode correctes

Niveau 2 (N2) : LLM mocké
  - LLM appelé si confiance heuristique < THRESHOLD
  - Fallback si LLM renvoie une réponse invalide
  - Fallback si ollama lève une exception

Niveau 3 (N3) : NoteWriterCrew.run() avec classifier intégré
  - Message court → Bucket A → crew().kickoff() appelé
  - Gros document → Bucket B → ingest_text_block() appelé, kickoff pas appelé
  - Idempotence : already_ingested → message dédié
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Mnemo.tools.memory_classifier import (
    ClassificationResult,
    classify_content,
    _heuristic,
    _llm_classify,
    CLASSIFIER_CONFIDENCE_THRESHOLD,
    BUCKET_B_TOKEN_THRESHOLD,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _big_text(words: int = BUCKET_B_TOKEN_THRESHOLD * 4 + 50) -> str:
    """Génère un texte de N mots sans signaux de structure."""
    return " ".join(["mot"] * words)


def _structured_text() -> str:
    return "## Architecture\n```python\ndef foo(): pass\n```\n---\nSpec complète."


# ══════════════════════════════════════════════════════════════════════════════
# N1 — Heuristique : Bucket A
# ══════════════════════════════════════════════════════════════════════════════

class TestHeuristicBucketA:

    def test_court_sans_signaux(self):
        r = _heuristic("je préfère vim")
        assert r.bucket == "A"

    def test_marker_a_note_que(self):
        r = _heuristic("note que je préfère vim")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_marker_a_retiens_que(self):
        r = _heuristic("retiens que mon projet s'appelle Mnemo")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_marker_a_souviens_toi(self):
        r = _heuristic("souviens-toi que j'ai un chat")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_marker_a_memorise_que(self):
        r = _heuristic("mémorise que j'utilise FastAPI")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_marker_a_garde_en_memoire(self):
        r = _heuristic("garde en mémoire que je préfère le thé")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_pronoms_personnels_bascule_vers_a(self):
        # Texte court avec "je" → doit rester A même sans marker explicite
        r = _heuristic("je travaille en remote depuis 2021")
        assert r.bucket == "A"

    def test_method_heuristic(self):
        r = _heuristic("note que je suis développeur")
        assert r.method == "heuristic"

    def test_confidence_between_0_and_1(self):
        for text in ["bonjour", "note que j'aime le café", _big_text()]:
            r = _heuristic(text)
            assert 0.0 <= r.confidence <= 1.0

    def test_reason_not_empty(self):
        r = _heuristic("note que je préfère vim")
        assert r.reason


# ══════════════════════════════════════════════════════════════════════════════
# N1 — Heuristique : Bucket B
# ══════════════════════════════════════════════════════════════════════════════

class TestHeuristicBucketB:

    def test_code_block(self):
        r = _heuristic("voici le code :\n```python\ndef foo(): pass\n```")
        assert r.bucket == "B"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_markdown_headers(self):
        r = _heuristic("## Architecture\n### Backend\nDétails ici.")
        assert r.bucket == "B"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_grand_texte(self):
        # Texte de ~1500 tokens sans structure → B par taille
        r = _heuristic(_big_text())
        assert r.bucket == "B"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_marker_b_voici(self):
        r = _heuristic("voici les specs du projet : " + "a " * 50)
        assert r.bucket == "B"

    def test_marker_b_ingere(self):
        r = _heuristic("ingère ce document : " + "x " * 20)
        assert r.bucket == "B"

    def test_structure_multiple_signaux(self):
        r = _heuristic(_structured_text())
        assert r.bucket == "B"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_method_heuristic(self):
        r = _heuristic(_structured_text())
        assert r.method == "heuristic"


# ══════════════════════════════════════════════════════════════════════════════
# N1 — Cas limites
# ══════════════════════════════════════════════════════════════════════════════

class TestHeuristicEdgeCases:

    def test_chaine_vide(self):
        r = _heuristic("")
        assert r.bucket in ("A", "B")  # pas d'exception
        assert 0.0 <= r.confidence <= 1.0

    def test_marker_a_sur_texte_long(self):
        # Marker A fort + texte long → le marker A doit peser plus
        long_text = "note que " + "j'ai un projet. " * 100
        r = _heuristic(long_text)
        # Marker A + pronoms → probablement A malgré taille, ou confiance basse
        # On vérifie juste que ça ne plante pas
        assert r.bucket in ("A", "B")

    def test_score_borne_0_1(self):
        # Score ne doit jamais sortir de [0, 1] même avec beaucoup de signaux
        text = "voici ```\n## \n### \n---\n" * 10
        r = _heuristic(text)
        assert 0.0 <= r.confidence <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# N2 — LLM mocké
# ══════════════════════════════════════════════════════════════════════════════

class TestLlmClassify:
    """
    Tests de _llm_classify via patch de mc._ollama (importé au niveau module).
    Zéro appel réseau réel.
    """

    def test_llm_retourne_a(self):
        """_llm_classify → A quand ollama répond 'A'."""
        import Mnemo.tools.memory_classifier as mc
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = {"message": {"content": "A"}}
        with patch.object(mc, "_ollama", mock_ollama):
            r = mc._llm_classify("texte ambigu")
        assert r is not None
        assert r.bucket == "A"
        assert r.method == "llm"
        assert r.confidence == 0.85

    def test_llm_retourne_b(self):
        """_llm_classify → B quand ollama répond 'B'."""
        import Mnemo.tools.memory_classifier as mc
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = {"message": {"content": "B"}}
        with patch.object(mc, "_ollama", mock_ollama):
            r = mc._llm_classify("texte ambigu")
        assert r is not None
        assert r.bucket == "B"
        assert r.method == "llm"

    def test_llm_exception_retourne_none(self):
        """Si ollama plante, _llm_classify retourne None (→ fallback activé)."""
        import Mnemo.tools.memory_classifier as mc
        mock_ollama = MagicMock()
        mock_ollama.chat.side_effect = RuntimeError("Ollama unavailable")
        with patch.object(mc, "_ollama", mock_ollama):
            r = mc._llm_classify("texte quelconque")
        assert r is None

    def test_llm_reponse_invalide_retourne_none(self):
        """Si ollama répond autre chose que A/B, _llm_classify retourne None."""
        import Mnemo.tools.memory_classifier as mc
        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = {"message": {"content": "inconnu"}}
        with patch.object(mc, "_ollama", mock_ollama):
            r = mc._llm_classify("texte quelconque")
        assert r is None

    def test_fallback_si_llm_none(self):
        """Si _llm_classify retourne None, classify_content active le fallback."""
        import Mnemo.tools.memory_classifier as mc

        # Texte ambigu : ~160 tokens (entre THRESHOLD/2 et THRESHOLD)
        # → heuristique donne confidence faible, LLM None → fallback
        ambiguous = "j'ai une spec technique : " + "détail " * 60

        with patch.object(mc, "_llm_classify", return_value=None):
            r = mc.classify_content(ambiguous)
        assert r.bucket in ("A", "B")
        assert r.method in ("heuristic", "fallback")
        assert 0.0 <= r.confidence <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# N2 — classify_content : pipeline complet
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyContent:

    def test_court_personnel_retourne_a(self):
        r = classify_content("note que je préfère vim")
        assert r.bucket == "A"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_gros_doc_retourne_b(self):
        r = classify_content(_big_text())
        assert r.bucket == "B"
        assert r.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD

    def test_structure_retourne_b(self):
        r = classify_content(_structured_text())
        assert r.bucket == "B"

    def test_confiance_toujours_valide(self):
        for text in [
            "bonjour",
            "note que j'aime le café",
            _big_text(),
            _structured_text(),
            "",
        ]:
            r = classify_content(text)
            assert 0.0 <= r.confidence <= 1.0
            assert r.bucket in ("A", "B")

    def test_method_heuristic_si_confiance_haute(self):
        r = classify_content("note que je préfère vim")
        assert r.method == "heuristic"

    def test_method_fallback_si_llm_none(self):
        """Texte ambigu + LLM None → fallback."""
        import Mnemo.tools.memory_classifier as mc
        ambiguous = "et puis " * 80  # ~320 tokens, pas de signaux forts

        with patch.object(mc, "_llm_classify", return_value=None):
            r = mc.classify_content(ambiguous)
        # La méthode doit être heuristic (si conf >= threshold) ou fallback
        assert r.method in ("heuristic", "fallback")


# ══════════════════════════════════════════════════════════════════════════════
# N3 — NoteWriterCrew.run() intégré
# ══════════════════════════════════════════════════════════════════════════════

class TestNoteWriterCrewWithClassifier:

    def _mock_kickoff(self, text="Note enregistrée.") -> MagicMock:
        m = MagicMock()
        m.raw = text
        return m

    def test_bucket_a_appelle_kickoff(self):
        """Message court personnel → Bucket A → crew.kickoff() appelé."""
        from Mnemo.crew import NoteWriterCrew
        import Mnemo.tools.memory_classifier as mc

        with patch.object(mc, "classify_content", return_value=ClassificationResult(
            bucket="A", confidence=0.90, method="heuristic", reason="marker A"
        )):
            with patch.object(NoteWriterCrew, "crew") as mock_crew_method:
                mock_crew_obj = MagicMock()
                mock_crew_method.return_value = mock_crew_obj
                mock_crew_obj.kickoff.return_value = self._mock_kickoff("Noté.")

                result = NoteWriterCrew().run({"user_message": "note que je préfère vim"})

        mock_crew_obj.kickoff.assert_called_once()
        assert result == "Noté."

    def test_bucket_b_appelle_ingest_pas_kickoff(self):
        """Gros document → Bucket B → ingest_text_block() appelé, kickoff non appelé."""
        from Mnemo.crew import NoteWriterCrew
        import Mnemo.tools.memory_classifier as mc
        import Mnemo.tools.ingest_tools as it

        with patch.object(mc, "classify_content", return_value=ClassificationResult(
            bucket="B", confidence=0.92, method="heuristic", reason="taille"
        )):
            with patch.object(it, "ingest_text_block", return_value={
                "status": "ingested", "chunks": 5, "doc_id": "abc", "filename": "bloc", "pages": 1
            }) as mock_ingest:
                with patch.object(NoteWriterCrew, "crew") as mock_crew_method:
                    mock_crew_obj = MagicMock()
                    mock_crew_method.return_value = mock_crew_obj

                    result = NoteWriterCrew().run({"user_message": _big_text()})

        mock_ingest.assert_called_once()
        mock_crew_obj.kickoff.assert_not_called()
        assert "5 chunks" in result

    def test_bucket_b_already_ingested(self):
        """Contenu déjà présent en DB → message dédié."""
        from Mnemo.crew import NoteWriterCrew
        import Mnemo.tools.memory_classifier as mc
        import Mnemo.tools.ingest_tools as it

        with patch.object(mc, "classify_content", return_value=ClassificationResult(
            bucket="B", confidence=0.92, method="heuristic", reason="taille"
        )):
            with patch.object(it, "ingest_text_block", return_value={
                "status": "already_ingested", "chunks": 0, "doc_id": "abc",
                "filename": "bloc", "pages": 0
            }):
                result = NoteWriterCrew().run({"user_message": _big_text()})

        assert "déjà" in result.lower()

    def test_bucket_b_vide(self):
        """Texte vide classifié B → message d'ingestion vide."""
        from Mnemo.crew import NoteWriterCrew
        import Mnemo.tools.memory_classifier as mc
        import Mnemo.tools.ingest_tools as it

        with patch.object(mc, "classify_content", return_value=ClassificationResult(
            bucket="B", confidence=0.92, method="heuristic", reason="taille"
        )):
            with patch.object(it, "ingest_text_block", return_value={
                "status": "empty", "chunks": 0, "doc_id": "", "filename": "", "pages": 0
            }):
                result = NoteWriterCrew().run({"user_message": ""})

        assert "vide" in result.lower() or "rien" in result.lower()

    def test_bucket_a_retourne_raw_strip(self):
        """Bucket A → résultat = raw.strip() du kickoff."""
        from Mnemo.crew import NoteWriterCrew
        import Mnemo.tools.memory_classifier as mc

        with patch.object(mc, "classify_content", return_value=ClassificationResult(
            bucket="A", confidence=0.90, method="heuristic", reason="marker A"
        )):
            with patch.object(NoteWriterCrew, "crew") as mock_crew_method:
                mock_crew_obj = MagicMock()
                mock_crew_method.return_value = mock_crew_obj
                mock_crew_obj.kickoff.return_value = self._mock_kickoff("  Préférence enregistrée.  ")

                result = NoteWriterCrew().run({"user_message": "note que je préfère vim"})

        assert result == "Préférence enregistrée."