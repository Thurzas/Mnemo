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

from fastapi import FastAPI, HTTPException, Request, Response

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


@app.post("/convert")
async def convert(request: Request) -> Response:
    """
    Reçoit un WAV brut (body = bytes), applique la conversion RVC,
    retourne le WAV converti (audio/wav).
    """
    try:
        rvc = _load_model()
    except Exception as exc:
        raise HTTPException(503, f"Modèle RVC non disponible : {exc}") from exc

    wav_bytes = await request.body()
    if not wav_bytes:
        raise HTTPException(400, "Body vide — attendu : bytes WAV")

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