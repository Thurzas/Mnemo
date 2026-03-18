"""
rvc_service/main.py — Micro-service RVC (Retrieval-based Voice Conversion)

Expose :
  GET  /health    → {"status": "ok", "model_loaded": bool}
  POST /convert   → body: WAV bytes → réponse: WAV bytes convertis

Modèles résolus depuis :
  1. Variables d'env RVC_MODEL_PATH / RVC_INDEX_PATH (chemin explicite)
  2. MODELS_DIR/*.pth (défaut : /models — volume monté depuis src/Voices/ ou /data/models/rvc/)

Paramètres RVC configurables via env vars :
  RVC_F0_METHOD   : pm | harvest | rmvpe  (défaut : harvest)
  RVC_F0_UP_KEY   : décalage tonal en demi-tons (défaut : 0)
  RVC_INDEX_RATE  : 0.0–1.0 (défaut : 0.75)
"""
from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

# ── Configuration ──────────────────────────────────────────────────
_MODELS_DIR     = Path(os.getenv("MODELS_DIR", "/models"))
_RVC_DEVICE     = os.getenv("RVC_DEVICE", "cuda:0")
_RVC_F0_METHOD  = os.getenv("RVC_F0_METHOD",  "harvest")
_RVC_F0_UP_KEY  = int(os.getenv("RVC_F0_UP_KEY",  "0"))
_RVC_INDEX_RATE = float(os.getenv("RVC_INDEX_RATE", "0.75"))

_rvc = None   # RVCInference, None si non chargé


def _resolve_paths() -> tuple[str, str]:
    """Retourne (model_path, index_path). Lève RuntimeError si aucun modèle trouvé."""
    # 1. Env vars explicites
    env_model = os.getenv("RVC_MODEL_PATH", "").strip()
    if env_model:
        return env_model, os.getenv("RVC_INDEX_PATH", "").strip()

    # 2. MODELS_DIR (volume monté)
    if _MODELS_DIR.exists():
        pth_files = sorted(_MODELS_DIR.glob("*.pth"))
        if pth_files:
            idx_files = sorted(_MODELS_DIR.glob("*.index"))
            return str(pth_files[0]), str(idx_files[0]) if idx_files else ""

    raise RuntimeError(
        f"Aucun modèle RVC (.pth) trouvé dans {_MODELS_DIR}. "
        "Montez vos fichiers via MODELS_DIR ou définissez RVC_MODEL_PATH."
    )


def _load_model():
    global _rvc
    if _rvc is not None:
        return _rvc

    model_path, index_path = _resolve_paths()

    from rvc_python.infer import RVCInference  # noqa: PLC0415
    log.info("Chargement du modèle RVC '%s'…", model_path)
    rvc = RVCInference(device=_RVC_DEVICE)
    rvc.load_model(model_path, index_path=index_path or "")
    rvc.set_params(
        f0method     = _RVC_F0_METHOD,
        f0up_key     = _RVC_F0_UP_KEY,
        index_rate   = _RVC_INDEX_RATE,
        filter_radius= 3,
        resample_sr  = 0,
        rms_mix_rate = 0.25,
        protect      = 0.33,
    )
    log.info("Modèle RVC chargé (index=%s).", index_path or "aucun")
    _rvc = rvc
    return rvc


# ── Lifespan : chargement du modèle au démarrage ──────────────────

@asynccontextmanager
async def lifespan(app_: FastAPI):
    try:
        _load_model()
    except Exception as exc:
        log.error("Modèle RVC non chargé au démarrage : %s", exc)
        log.warning("Le service démarrera quand même — /convert retournera 503 jusqu'à résolution.")
    yield


# ── Application ────────────────────────────────────────────────────

app = FastAPI(
    title="RVC Voice Conversion Service",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": _rvc is not None}


@app.get("/params")
async def get_params():
    """Retourne les paramètres RVC actifs (valeurs env par défaut)."""
    return {
        "f0_method":     _RVC_F0_METHOD,
        "f0_up_key":     _RVC_F0_UP_KEY,
        "index_rate":    _RVC_INDEX_RATE,
        "filter_radius": 3,
        "rms_mix_rate":  0.25,
        "protect":       0.33,
    }


@app.post("/reload")
async def reload_model(
    model_path: str = Query(...),
    index_path: str = Query(""),
) -> dict:
    """
    Recharge le modèle RVC depuis les chemins donnés.
    Utilisé par l'API Mnemo après upload d'un nouveau modèle via l'UI.
    """
    global _rvc
    p = Path(model_path)
    if not p.exists():
        raise HTTPException(400, f"Modèle introuvable : {model_path}")
    log.info("Rechargement du modèle RVC '%s'…", model_path)
    _rvc = None
    os.environ["RVC_MODEL_PATH"] = model_path
    os.environ["RVC_INDEX_PATH"] = index_path
    try:
        _load_model()
    except Exception as exc:
        raise HTTPException(500, f"Erreur chargement modèle : {exc}") from exc
    return {"ok": True, "model_path": model_path, "index_path": index_path}


@app.post("/convert")
async def convert(
    request: Request,
    f0_method:     Optional[str]   = Query(None),
    f0_up_key:     Optional[int]   = Query(None),
    index_rate:    Optional[float] = Query(None),
    filter_radius: Optional[int]   = Query(None),
    rms_mix_rate:  Optional[float] = Query(None),
    protect:       Optional[float] = Query(None),
) -> Response:
    """
    Reçoit un WAV brut (body = bytes), applique la conversion RVC.

    Query params optionnels — surchargent les valeurs env pour cette requête :
      ?f0_method=rmvpe&f0_up_key=0&index_rate=0.75
    """
    try:
        rvc = _load_model()
    except Exception as exc:
        raise HTTPException(503, f"Modèle RVC non disponible : {exc}") from exc

    wav_bytes = await request.body()
    if not wav_bytes:
        raise HTTPException(400, "Body vide — attendu : bytes WAV")

    override: dict = {}
    if f0_method     is not None: override["f0method"]      = f0_method
    if f0_up_key     is not None: override["f0up_key"]      = f0_up_key
    if index_rate    is not None: override["index_rate"]    = index_rate
    if filter_radius is not None: override["filter_radius"] = filter_radius
    if rms_mix_rate  is not None: override["rms_mix_rate"]  = rms_mix_rate
    if protect       is not None: override["protect"]       = protect
    if override:
        rvc.set_params(**override)

    in_tmp = out_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            in_tmp = f.name
        out_tmp = in_tmp.replace(".wav", "_rvc.wav")

        rvc.infer_file(in_tmp, out_tmp)
        with open(out_tmp, "rb") as f:
            result = f.read()
        return Response(content=result, media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as exc:
        log.error("Erreur conversion RVC : %s", exc)
        raise HTTPException(500, f"Erreur conversion RVC : {exc}") from exc
    finally:
        for p in (in_tmp, out_tmp):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass