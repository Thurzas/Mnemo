"""
Tests — routes API assistant (GET/PUT /api/assistant)

Couvre :
  - GET : utilisateur sans fichier → defaults (name=Mnemo)
  - GET : utilisateur avec fichier → données correctes
  - PUT : met à jour les champs fournis, retourne la config à jour
  - PUT : champs absents (non fournis) ne font pas partie du body → ignorés
  - PUT : body avec tous les champs None → retourne 400

Logique simulée (même convention que test_goap_confirmations.py).
Aucun LLM requis, aucune instance FastAPI instanciée.
"""
import json
import pytest
from pathlib import Path

from Mnemo.tools.assistant_tools import (
    get_assistant_config,
    set_assistant_config,
)


# ── Helpers ────────────────────────────────────────────────────────

def _write_config(tmp_path: Path, data: dict, username: str = "Alice") -> Path:
    d = tmp_path / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    p = d / "assistant.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _simulate_get(username: str, data_path: Path) -> dict:
    """Reproduit la logique de GET /api/assistant."""
    return get_assistant_config(username, data_path)


def _simulate_put(username: str, data_path: Path, body: dict) -> dict | tuple[int, str]:
    """
    Reproduit la logique de PUT /api/assistant.
    Retourne (400, detail) si body vide, sinon la config mise à jour.
    """
    updates = {k: v for k, v in body.items() if v is not None}
    if not updates:
        return (400, "Aucun champ fourni")
    return set_assistant_config(username, data_path=data_path, **updates)


# ══════════════════════════════════════════════════════════════════════
# 1. GET /api/assistant
# ══════════════════════════════════════════════════════════════════════

class TestGetAssistant:

    def test_sans_fichier_retourne_defaults(self, tmp_path):
        result = _simulate_get("Alice", tmp_path)
        assert result["name"] == "Mnemo"
        assert "persona_full" in result
        assert "language_style" in result

    def test_avec_fichier_retourne_donnees(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "pronouns": "elle/la",
            "persona_short": "tsundere",
            "persona_full": "Tu es Mitsune.",
            "language_style": "Direct.",
        })
        result = _simulate_get("Alice", tmp_path)
        assert result["name"] == "Mitsune"
        assert result["pronouns"] == "elle/la"

    def test_isolation_entre_utilisateurs(self, tmp_path):
        _write_config(tmp_path, {"name": "Aria", "persona_full": ".", "language_style": "."}, username="Alice")
        _write_config(tmp_path, {"name": "Kira", "persona_full": ".", "language_style": "."}, username="Bob")

        alice = _simulate_get("Alice", tmp_path)
        bob   = _simulate_get("Bob",   tmp_path)
        assert alice["name"] == "Aria"
        assert bob["name"]   == "Kira"


# ══════════════════════════════════════════════════════════════════════
# 2. PUT /api/assistant — mise à jour
# ══════════════════════════════════════════════════════════════════════

class TestPutAssistant:

    def test_met_a_jour_le_nom(self, tmp_path):
        result = _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        assert isinstance(result, dict)
        assert result["name"] == "Mitsune"

    def test_persiste_sur_disque(self, tmp_path):
        _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        reloaded = get_assistant_config("Alice", data_path=tmp_path)
        assert reloaded["name"] == "Mitsune"

    def test_met_a_jour_plusieurs_champs(self, tmp_path):
        result = _simulate_put("Alice", tmp_path, {
            "name": "Aria",
            "pronouns": "elle/la",
            "persona_short": "kuudere",
            "language_style": "Calme et posé.",
        })
        assert result["name"] == "Aria"
        assert result["pronouns"] == "elle/la"
        assert result["language_style"] == "Calme et posé."

    def test_preserve_champs_non_fournis(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "pronouns": "elle/la",
            "persona_full": "Persona existant.",
            "language_style": "Direct.",
        })
        result = _simulate_put("Alice", tmp_path, {"name": "Kira"})
        assert result["pronouns"] == "elle/la"
        assert result["persona_full"] == "Persona existant."

    def test_valeurs_none_ignorees(self, tmp_path):
        _write_config(tmp_path, {"name": "Mitsune", "persona_full": ".", "language_style": "."})
        result = _simulate_put("Alice", tmp_path, {"name": None, "pronouns": None})
        # Tous à None → 400
        assert result == (400, "Aucun champ fourni")

    def test_body_vide_retourne_400(self, tmp_path):
        result = _simulate_put("Alice", tmp_path, {})
        assert result == (400, "Aucun champ fourni")

    def test_champs_mixtes_none_et_valeur(self, tmp_path):
        _write_config(tmp_path, {"name": "Mitsune", "persona_full": ".", "language_style": "."})
        # name=None ignoré, pronouns valide → update réussit
        result = _simulate_put("Alice", tmp_path, {"name": None, "pronouns": "elle/la"})
        assert isinstance(result, dict)
        assert result["pronouns"] == "elle/la"
        assert result["name"] == "Mitsune"  # inchangé

    def test_updated_at_ajoute(self, tmp_path):
        result = _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        assert "updated_at" in result

    def test_cree_fichier_si_inexistant(self, tmp_path):
        result = _simulate_put("NewUser", tmp_path, {"name": "Zara"})
        assert isinstance(result, dict)
        assert result["name"] == "Zara"
        p = tmp_path / "users" / "NewUser" / "assistant.json"
        assert p.exists()


# ══════════════════════════════════════════════════════════════════════
# 3. Cohérence GET après PUT
# ══════════════════════════════════════════════════════════════════════

class TestGetApresPut:

    def test_get_reflète_put(self, tmp_path):
        _simulate_put("Alice", tmp_path, {"name": "Mitsune", "pronouns": "elle/la"})
        cfg = _simulate_get("Alice", tmp_path)
        assert cfg["name"] == "Mitsune"
        assert cfg["pronouns"] == "elle/la"

    def test_double_put_idempotent(self, tmp_path):
        _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        cfg = _simulate_get("Alice", tmp_path)
        assert cfg["name"] == "Mitsune"

    def test_put_sequentiels_accumulent(self, tmp_path):
        _simulate_put("Alice", tmp_path, {"name": "Mitsune"})
        _simulate_put("Alice", tmp_path, {"pronouns": "elle/la"})
        cfg = _simulate_get("Alice", tmp_path)
        assert cfg["name"] == "Mitsune"
        assert cfg["pronouns"] == "elle/la"
