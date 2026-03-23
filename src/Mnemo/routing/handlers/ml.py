"""
MLHandler — classifieur sklearn (router_model.joblib).

Court-circuite si la confiance dépasse les seuils par route.
Dépose ml_route / ml_conf dans ctx._hints pour que LLMHandler
puisse faire l'arbitrage sans couplage direct.

Active learning : les cas incertains (conf < UNCERTAIN_THRESHOLD)
sont loggués dans uncertain_cases.jsonl pour le prochain re-train.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..base import RouterHandler
from ..context import RouterContext, RouterResult

# ── Seuils de confiance par route ─────────────────────────────────────────────
_THRESHOLDS: dict[str, float] = {
    "shell":     0.80,
    "scheduler": 0.75,
    "calendar":  0.92,
    "plan":      0.75,
}
_DEFAULT_THRESHOLD      = 0.70
_ULTRA_CONF_THRESHOLD   = 0.95   # bypass total LLM (ML quasi-certain)
_UNCERTAIN_THRESHOLD    = 0.70   # en dessous = cas à loguer

# ── Active learning ──────────────────────────────────────────────────────────
_UNCERTAIN_LOG = Path("uncertain_cases.jsonl")  # relatif à WORKDIR=/data


def _log_uncertain(message: str, final_route: str, ml_conf: float) -> None:
    """
    Logge les messages où le ML était peu confiant (conf < seuil).
    La route finale (déterminée en aval) sert de label pour le re-train.
    """
    if ml_conf >= _UNCERTAIN_THRESHOLD:
        return
    try:
        entry = json.dumps({
            "text":    message,
            "route":   final_route,
            "ml_conf": round(ml_conf, 3),
            "source":  "active_learning",
        }, ensure_ascii=False)
        with open(_UNCERTAIN_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ── Chargement du modèle ──────────────────────────────────────────────────────
_ROUTER_MODEL = None


def _load_router_model():
    global _ROUTER_MODEL
    if _ROUTER_MODEL is not None:
        return _ROUTER_MODEL
    # Cherche dans /data d'abord (retrain sans rebuild), fallback /app
    model_path = Path("router_model.joblib")
    if not model_path.exists():
        model_path = Path(__file__).parent.parent.parent / "router_model.joblib"
    if not model_path.exists():
        return None
    try:
        import joblib
        _ROUTER_MODEL = joblib.load(model_path)
        n = _ROUTER_MODEL.get("n_train", "?")
        print(f"  [router ML] modele charge ({n} exemples)")
        return _ROUTER_MODEL
    except Exception as e:
        print(f"  [router ML] echec : {e}")
        return None


def _ml_predict(message: str) -> tuple[str, float]:
    """Retourne (route, confidence). confidence=0.0 si modèle absent."""
    md = _load_router_model()
    if md is None:
        return "conversation", 0.0
    try:
        pipeline = md["pipeline"]
        proba    = pipeline.predict_proba([message])[0]
        max_idx  = proba.argmax()
        conf     = float(proba[max_idx])
        if conf < 0.40:
            return "conversation", 0.0
        return md["routes"][max_idx], conf
    except Exception:
        return "conversation", 0.0


# ── Handler ───────────────────────────────────────────────────────────────────

class MLHandler(RouterHandler):
    """
    Classifieur sklearn — court-circuite la chaîne si confiance >= seuil.

    Cas de bypass direct (sans LLM) :
      - ML ultra-confiant (>= 0.95)
      - kw_shell hint + ML shell >= 0.80
      - kw_sched_weak hint + ML scheduler >= 0.80

    Sinon : dépose ml_route / ml_conf dans ctx._hints et délègue au LLMHandler.
    """

    def handle(self, ctx: RouterContext) -> RouterResult | None:
        ml_route, ml_conf = _ml_predict(ctx.message)

        # Dépôt des hints pour LLMHandler (arbitrage)
        ctx._hints["ml_route"] = ml_route
        ctx._hints["ml_conf"]  = ml_conf

        kw_shell      = ctx._hints.get("kw_shell", False)
        kw_sched_weak = ctx._hints.get("kw_sched_weak", False)
        kw_plan_weak  = ctx._hints.get("kw_plan_weak", False)

        # ── Bypass total (ML quasi-certain) ────────────────────────────────
        if ml_conf >= _ULTRA_CONF_THRESHOLD:
            return RouterResult(
                ml_route, ml_conf, "ml",
                metadata={"ml_conf": ml_conf, "bypass": "ultra"},
            )

        # ── Bypass ML + keyword concordant ────────────────────────────────
        if kw_shell and ml_route == "shell" and ml_conf >= _THRESHOLDS["shell"]:
            return RouterResult(
                "shell", ml_conf, "ml",
                metadata={"ml_conf": ml_conf, "bypass": "kw+ml"},
            )
        if kw_sched_weak and ml_route == "scheduler" and ml_conf >= _THRESHOLDS["scheduler"]:
            return RouterResult(
                "scheduler", ml_conf, "ml",
                metadata={"ml_conf": ml_conf, "bypass": "kw+ml"},
            )
        if kw_plan_weak and ml_route == "plan" and ml_conf >= _THRESHOLDS["plan"]:
            return RouterResult(
                "plan", ml_conf, "ml",
                metadata={"ml_conf": ml_conf, "bypass": "kw+ml", "needs_recon": True},
            )

        # ── Confiance insuffisante → délègue au LLM ───────────────────────
        return self._pass(ctx)