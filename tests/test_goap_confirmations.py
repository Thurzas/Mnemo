"""
Tests Phase 7.6 — Confirmation d'actions GOAP

Couvre :
  - Lecture des pending_confirmations depuis world_state.json
  - Suppression d'une confirmation (approbation ET rejet)
  - Extraction de commande depuis un label "sandbox_shell: <cmd>"
  - run_command appelé avec le bon slug et la bonne commande
  - Action non-shell → pas d'exécution
  - Confirmation inconnue → KeyError gérable

Aucun LLM requis.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Helpers ────────────────────────────────────────────────────────

def _write_world_state(path: Path, confirmations: list) -> None:
    path.write_text(
        json.dumps({"pending_confirmations": confirmations}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_world_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_confirmation(id_: str, action: str, slug: str = "mon-projet") -> dict:
    return {
        "id":           id_,
        "action":       action,
        "project_slug": slug,
        "step_label":   "étape test",
        "description":  f"Exécuter '{action}'",
        "ts":           "2026-03-20T12:00:00",
    }


# ── Simulation de la logique de confirmation ──────────────────────
# (même logique que le handler /api/confirmations/{id})

def _simulate_confirm(ws_path: Path, confirmation_id: str, approved: bool) -> dict:
    """
    Reproduit la logique du handler FastAPI pour les tests unitaires.
    Retourne le même dict que le handler.
    """
    ws = _read_world_state(ws_path)
    confirmations: list = ws.get("pending_confirmations", [])
    target = next((c for c in confirmations if c.get("id") == confirmation_id), None)
    if target is None:
        raise KeyError(f"Confirmation '{confirmation_id}' introuvable")

    ws["pending_confirmations"] = [c for c in confirmations if c.get("id") != confirmation_id]
    ws_path.write_text(json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8")

    if not approved:
        return {"ok": True, "executed": False, "stdout": "", "stderr": "", "returncode": None}

    action = target.get("action", "")
    slug   = target.get("project_slug", "")

    if action.startswith("sandbox_shell:"):
        command = action[len("sandbox_shell:"):].strip()
        from Mnemo.tools.sandbox_tools import run_command
        result = run_command(slug, command)
        return {
            "ok":         result["returncode"] == 0,
            "executed":   True,
            "stdout":     result.get("stdout", ""),
            "stderr":     result.get("stderr", ""),
            "returncode": result["returncode"],
        }

    return {"ok": True, "executed": False, "stdout": "",
            "stderr": f"Action non exécutable depuis l'API : {action}", "returncode": None}


# ══════════════════════════════════════════════════════════════════════════════
# 1. Lecture des confirmations
# ══════════════════════════════════════════════════════════════════════════════

class TestReadConfirmations:

    def test_world_state_absent(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        assert not ws_path.exists()
        # Reproduit la logique GET /api/confirmations
        if not ws_path.exists():
            result = {"confirmations": []}
        else:
            ws = json.loads(ws_path.read_text())
            result = {"confirmations": ws.get("pending_confirmations", [])}
        assert result == {"confirmations": []}

    def test_world_state_vide(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        ws_path.write_text(json.dumps({}))
        ws = json.loads(ws_path.read_text())
        assert ws.get("pending_confirmations", []) == []

    def test_confirmations_lues(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        confs = [
            _make_confirmation("abc123", "sandbox_shell: npm install"),
            _make_confirmation("def456", "sandbox_shell: pytest"),
        ]
        _write_world_state(ws_path, confs)
        ws = _read_world_state(ws_path)
        assert len(ws["pending_confirmations"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 2. Rejet d'une confirmation
# ══════════════════════════════════════════════════════════════════════════════

class TestRejectConfirmation:

    def test_rejet_supprime_confirmation(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("x1", "sandbox_shell: npm install")])
        _simulate_confirm(ws_path, "x1", approved=False)
        ws = _read_world_state(ws_path)
        assert ws["pending_confirmations"] == []

    def test_rejet_retourne_executed_false(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("x1", "sandbox_shell: npm install")])
        result = _simulate_confirm(ws_path, "x1", approved=False)
        assert result["executed"] is False

    def test_rejet_preserve_autres_confirmations(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [
            _make_confirmation("keep1", "sandbox_shell: npm install"),
            _make_confirmation("del1",  "sandbox_shell: pytest"),
        ])
        _simulate_confirm(ws_path, "del1", approved=False)
        ws = _read_world_state(ws_path)
        ids = [c["id"] for c in ws["pending_confirmations"]]
        assert "keep1" in ids
        assert "del1" not in ids

    def test_confirmation_inconnue_leve_keyerror(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("real", "sandbox_shell: npm install")])
        with pytest.raises(KeyError):
            _simulate_confirm(ws_path, "ghost", approved=False)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Approbation — extraction de commande
# ══════════════════════════════════════════════════════════════════════════════

class TestCommandExtraction:

    def test_sandbox_shell_simple(self):
        action = "sandbox_shell: npm install"
        assert action.startswith("sandbox_shell:")
        cmd = action[len("sandbox_shell:"):].strip()
        assert cmd == "npm install"

    def test_sandbox_shell_avec_espaces(self):
        action = "sandbox_shell:   pytest -v tests/"
        cmd = action[len("sandbox_shell:"):].strip()
        assert cmd == "pytest -v tests/"

    def test_action_non_shell_pas_extrait(self):
        action = "web_fetch"
        assert not action.startswith("sandbox_shell:")

    def test_action_write_pas_extrait(self):
        action = "sandbox_write"
        assert not action.startswith("sandbox_shell:")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Approbation — exécution via run_command
# ══════════════════════════════════════════════════════════════════════════════

class TestApproveExecution:

    def test_run_command_appele_avec_bonne_commande(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("r1", "sandbox_shell: echo hello", slug="test-slug")])

        mock_result = {"stdout": "hello\n", "stderr": "", "returncode": 0, "error": None}
        with patch("Mnemo.tools.sandbox_tools.run_command", return_value=mock_result) as mock_run:
            result = _simulate_confirm(ws_path, "r1", approved=True)

        mock_run.assert_called_once_with("test-slug", "echo hello")
        assert result["executed"] is True
        assert result["returncode"] == 0
        assert result["stdout"] == "hello\n"

    def test_run_command_echec_ok_false(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("r2", "sandbox_shell: exit 1")])
        mock_result = {"stdout": "", "stderr": "erreur", "returncode": 1, "error": None}
        with patch("Mnemo.tools.sandbox_tools.run_command", return_value=mock_result):
            result = _simulate_confirm(ws_path, "r2", approved=True)
        assert result["ok"] is False
        assert result["returncode"] == 1

    def test_approbation_supprime_confirmation(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("r3", "sandbox_shell: echo ok")])
        mock_result = {"stdout": "ok\n", "stderr": "", "returncode": 0, "error": None}
        with patch("Mnemo.tools.sandbox_tools.run_command", return_value=mock_result):
            _simulate_confirm(ws_path, "r3", approved=True)
        ws = _read_world_state(ws_path)
        assert ws["pending_confirmations"] == []

    def test_action_non_shell_executed_false(self, tmp_path):
        ws_path = tmp_path / "world_state.json"
        _write_world_state(ws_path, [_make_confirmation("r4", "web_fetch")])
        result = _simulate_confirm(ws_path, "r4", approved=True)
        assert result["executed"] is False

    def test_world_state_existant_preserve(self, tmp_path):
        """Les autres clés du world_state ne sont pas écrasées."""
        ws_path = tmp_path / "world_state.json"
        ws = {"existing_key": "preserved", "pending_confirmations": [
            _make_confirmation("r5", "sandbox_shell: ls"),
        ]}
        ws_path.write_text(json.dumps(ws, ensure_ascii=False, indent=2))
        mock_result = {"stdout": ".", "stderr": "", "returncode": 0, "error": None}
        with patch("Mnemo.tools.sandbox_tools.run_command", return_value=mock_result):
            _simulate_confirm(ws_path, "r5", approved=True)
        updated = _read_world_state(ws_path)
        assert updated["existing_key"] == "preserved"