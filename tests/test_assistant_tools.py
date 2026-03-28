"""
Tests — assistant_tools.py

Couvre :
  - get_assistant_config : absent → défauts, corrompu → défauts, valide → données
  - ensure_assistant_config : crée le fichier si absent, ne touche pas si existant
  - set_assistant_config : met à jour les champs, préserve les autres, ajoute updated_at
  - get_assistant_context : formate le bloc identité, résout {name}
  - get_assistant_name : retourne le nom

Aucun LLM requis.
"""
import json
import pytest
from pathlib import Path

from Mnemo.tools.assistant_tools import (
    get_assistant_config,
    ensure_assistant_config,
    set_assistant_config,
    get_assistant_context,
    get_assistant_name,
)


# ── Helpers ────────────────────────────────────────────────────────

def _user_dir(tmp_path: Path, username: str = "TestUser") -> Path:
    d = tmp_path / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_config(tmp_path: Path, data: dict, username: str = "TestUser") -> Path:
    p = _user_dir(tmp_path, username) / "assistant.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════════════
# 1. get_assistant_config
# ══════════════════════════════════════════════════════════════════════

class TestGetAssistantConfig:

    def test_fichier_absent_retourne_defaults(self, tmp_path):
        cfg = get_assistant_config("Ghost", data_path=tmp_path)
        assert cfg["name"] == "Mnemo"
        assert "persona_full" in cfg
        assert "{name}" not in cfg["persona_full"]  # placeholder résolu

    def test_fichier_corrompu_retourne_defaults(self, tmp_path):
        p = _user_dir(tmp_path) / "assistant.json"
        p.write_text("not { valid json", encoding="utf-8")
        cfg = get_assistant_config("TestUser", data_path=tmp_path)
        assert cfg["name"] == "Mnemo"

    def test_fichier_valide_retourne_donnees(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "pronouns": "elle/la",
            "persona_short": "tsundere",
            "persona_full": "Tu es Mitsune.",
            "language_style": "Direct.",
        })
        cfg = get_assistant_config("TestUser", data_path=tmp_path)
        assert cfg["name"] == "Mitsune"
        assert cfg["pronouns"] == "elle/la"
        assert cfg["persona_full"] == "Tu es Mitsune."

    def test_data_path_isole(self, tmp_path):
        """data_path explicite → ne lit pas /data global."""
        cfg = get_assistant_config("nobody", data_path=tmp_path)
        assert cfg["name"] == "Mnemo"


# ══════════════════════════════════════════════════════════════════════
# 2. ensure_assistant_config
# ══════════════════════════════════════════════════════════════════════

class TestEnsureAssistantConfig:

    def test_cree_fichier_si_absent(self, tmp_path):
        cfg = ensure_assistant_config("NewUser", data_path=tmp_path)
        p = tmp_path / "users" / "NewUser" / "assistant.json"
        assert p.exists()
        assert cfg["name"] == "Mnemo"

    def test_created_at_present(self, tmp_path):
        cfg = ensure_assistant_config("NewUser", data_path=tmp_path)
        assert "created_at" in cfg

    def test_ne_modifie_pas_si_existant(self, tmp_path):
        _write_config(tmp_path, {"name": "Mitsune", "persona_full": "...", "language_style": "."})
        cfg1 = ensure_assistant_config("TestUser", data_path=tmp_path)
        cfg2 = ensure_assistant_config("TestUser", data_path=tmp_path)
        assert cfg1["name"] == cfg2["name"] == "Mitsune"

    def test_cree_repertoire_parents(self, tmp_path):
        ensure_assistant_config("deep/nested", data_path=tmp_path)
        p = tmp_path / "users" / "deep" / "nested" / "assistant.json"
        assert p.exists()


# ══════════════════════════════════════════════════════════════════════
# 3. set_assistant_config
# ══════════════════════════════════════════════════════════════════════

class TestSetAssistantConfig:

    def test_met_a_jour_le_nom(self, tmp_path):
        cfg = set_assistant_config("TestUser", data_path=tmp_path, name="Kira")
        assert cfg["name"] == "Kira"
        # Persisté sur disque
        reloaded = get_assistant_config("TestUser", data_path=tmp_path)
        assert reloaded["name"] == "Kira"

    def test_preserve_champs_non_touches(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "pronouns": "elle/la",
            "persona_full": "...",
            "language_style": "direct",
        })
        cfg = set_assistant_config("TestUser", data_path=tmp_path, name="Kira")
        assert cfg["pronouns"] == "elle/la"
        assert cfg["language_style"] == "direct"

    def test_ajoute_updated_at(self, tmp_path):
        cfg = set_assistant_config("TestUser", data_path=tmp_path, name="X")
        assert "updated_at" in cfg

    def test_update_plusieurs_champs(self, tmp_path):
        cfg = set_assistant_config(
            "TestUser",
            data_path=tmp_path,
            name="Aria",
            pronouns="elle/la",
            language_style="Concis.",
        )
        assert cfg["name"] == "Aria"
        assert cfg["pronouns"] == "elle/la"
        assert cfg["language_style"] == "Concis."

    def test_none_value_ignore(self, tmp_path):
        _write_config(tmp_path, {"name": "Mitsune", "persona_full": "...", "language_style": "."})
        cfg = set_assistant_config("TestUser", data_path=tmp_path, name=None)
        # name=None ne doit pas écraser le nom existant
        assert cfg["name"] == "Mitsune"

    def test_cree_fichier_si_absent(self, tmp_path):
        cfg = set_assistant_config("NewUser", data_path=tmp_path, name="Zara")
        assert cfg["name"] == "Zara"
        p = tmp_path / "users" / "NewUser" / "assistant.json"
        assert p.exists()


# ══════════════════════════════════════════════════════════════════════
# 4. get_assistant_context
# ══════════════════════════════════════════════════════════════════════

class TestGetAssistantContext:

    def test_contient_le_nom(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "persona_full": "Tu es Mitsune, une IA tsundere.",
            "language_style": "Direct.",
        })
        ctx = get_assistant_context("TestUser", data_path=tmp_path)
        assert "Mitsune" in ctx

    def test_placeholder_name_resolu(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mnemo",
            "persona_full": "Tu es {name}, un assistant.",
            "language_style": ".",
        })
        ctx = get_assistant_context("TestUser", data_path=tmp_path)
        assert "{name}" not in ctx
        assert "Mnemo" in ctx

    def test_contient_style(self, tmp_path):
        _write_config(tmp_path, {
            "name": "Mitsune",
            "persona_full": "Tu es Mitsune.",
            "language_style": "Style particulier xyz",
        })
        ctx = get_assistant_context("TestUser", data_path=tmp_path)
        assert "Style particulier xyz" in ctx

    def test_format_titre_identite(self, tmp_path):
        ctx = get_assistant_context("Ghost", data_path=tmp_path)
        assert "## Ton identité" in ctx

    def test_fallback_sans_fichier(self, tmp_path):
        ctx = get_assistant_context("Nobody", data_path=tmp_path)
        assert "Mnemo" in ctx
        assert "## Ton identité" in ctx


# ══════════════════════════════════════════════════════════════════════
# 5. get_assistant_name
# ══════════════════════════════════════════════════════════════════════

class TestGetAssistantName:

    def test_retourne_nom_config(self, tmp_path):
        _write_config(tmp_path, {"name": "Aria", "persona_full": ".", "language_style": "."})
        assert get_assistant_name("TestUser", data_path=tmp_path) == "Aria"

    def test_fallback_mnemo_si_absent(self, tmp_path):
        assert get_assistant_name("Ghost", data_path=tmp_path) == "Mnemo"
