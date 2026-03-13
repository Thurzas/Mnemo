"""
audio_tools.py — STT (Whisper) + TTS (Piper → RVC) pour Mnemo

Pipeline :
  STT : audio  →  faster-whisper  →  texte
  TTS : texte  →  Piper (voix neutre)  →  RVC (conversion voix custom)  →  audio

Modèles :
  Whisper  : tiny (~39 MB), téléchargé dans /data/models/whisper/ au 1er usage
  Piper    : fr_FR-siwis-medium (~65 MB), téléchargé dans /data/models/piper/ au 1er usage
  RVC      : résolution automatique (voir _rvc_paths())

Résolution des fichiers RVC (ordre de priorité) :
  1. Variables d'env RVC_MODEL_PATH + RVC_INDEX_PATH (chemin explicite)
  2. /data/models/rvc/*.pth + *.index  (volume Docker persistant)
  3. <package>/../../Voices/*.pth + *.index  (bundlé dans l'image, pour les tests)

Dépendances optionnelles :
  pip install faster-whisper piper-tts rvc-python
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# ── Lazy singletons ────────────────────────────────────────────────
_whisper_model = None
_piper_voice   = None
_rvc_infer     = None          # RVCInference instance, None si non configuré


def _models_dir() -> Path:
    """Répertoire des modèles audio — /data/models/ (volume persistant Docker)."""
    from Mnemo.context import get_data_dir
    return get_data_dir() / "models"


# ══════════════════════════════════════════════════════════════════
# STT — faster-whisper
# ══════════════════════════════════════════════════════════════════

_WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "tiny")


_APP_MODELS = Path("/app/models")   # modèles baked dans l'image Docker


def _whisper_cache_dir() -> str:
    """
    Priorité : /app/models/whisper (baked au build) →
               /data/models/whisper (volume, fallback download)
    """
    bundled = _APP_MODELS / "whisper"
    if bundled.exists():
        return str(bundled)
    d = _models_dir() / "whisper"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        import faster_whisper  # noqa: PLC0415
        cache_dir = _whisper_cache_dir()
        log.info("Chargement du modèle Whisper '%s'…", _WHISPER_MODEL_SIZE)
        _whisper_model = faster_whisper.WhisperModel(
            _WHISPER_MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            download_root=cache_dir,
        )
        log.info("Modèle Whisper chargé.")
    return _whisper_model


def transcribe_audio(audio_bytes: bytes, language: str = "fr") -> str:
    """Transcrit des bytes audio (webm, wav, mp3…) en texte."""
    model = _get_whisper()
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio_bytes)
        tmp = f.name
    try:
        segments, _info = model.transcribe(
            tmp,
            language=language,
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ══════════════════════════════════════════════════════════════════
# TTS étape 1 — Piper (texte → WAV neutre)
# ══════════════════════════════════════════════════════════════════

_PIPER_VOICE      = os.getenv("PIPER_VOICE", "fr_FR-siwis-medium")
_PIPER_VOICE_REPO = "rhasspy/piper-voices"
_PIPER_VOICE_TAG  = "v1.0.0"
_PIPER_VOICE_HF   = "fr/fr_FR/siwis/medium/fr_FR-siwis-medium"


def _piper_voice_paths() -> tuple[Path, Path]:
    """
    Priorité : /app/models/piper (baked au build) →
               /data/models/piper (volume, fallback download)
    """
    bundled_dir = _APP_MODELS / "piper"
    onnx_b   = bundled_dir / f"{_PIPER_VOICE}.onnx"
    config_b = bundled_dir / f"{_PIPER_VOICE}.onnx.json"
    if onnx_b.exists() and config_b.exists():
        return onnx_b, config_b

    # Fallback : volume /data (et téléchargement si absent)
    voice_dir = _models_dir() / "piper"
    voice_dir.mkdir(parents=True, exist_ok=True)
    onnx   = voice_dir / f"{_PIPER_VOICE}.onnx"
    config = voice_dir / f"{_PIPER_VOICE}.onnx.json"
    if not onnx.exists() or not config.exists():
        _download_piper_voice(onnx, config)
    return onnx, config


def _download_piper_voice(onnx: Path, config: Path) -> None:
    base = (
        f"https://huggingface.co/{_PIPER_VOICE_REPO}/resolve/"
        f"{_PIPER_VOICE_TAG}/{_PIPER_VOICE_HF}"
    )
    log.info("Téléchargement de la voix Piper '%s'…", _PIPER_VOICE)
    for path, suffix in [(onnx, ".onnx"), (config, ".onnx.json")]:
        log.info("  GET %s%s", base, suffix)
        urllib.request.urlretrieve(f"{base}{suffix}", str(path))
    log.info("Voix Piper téléchargée.")


def _get_piper():
    global _piper_voice
    if _piper_voice is None:
        from piper import PiperVoice  # noqa: PLC0415
        onnx, config = _piper_voice_paths()
        log.info("Chargement de la voix Piper '%s'…", _PIPER_VOICE)
        _piper_voice = PiperVoice.load(str(onnx), config_path=str(config), use_cuda=False)
        log.info("Voix Piper chargée.")
    return _piper_voice


def _piper_to_wav_bytes(text: str) -> bytes:
    """Piper : texte → bytes WAV (voix neutre française)."""
    import wave
    voice = _get_piper()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # 16-bit PCM
        wf.setframerate(voice.config.sample_rate)
        voice.synthesize_wav(text, wf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════
# TTS étape 2 — RVC (WAV neutre → WAV voix custom)
# ══════════════════════════════════════════════════════════════════

def _rvc_paths() -> tuple[str, str]:
    """
    Résout (model_path, index_path) selon la priorité suivante :
    1. Variables d'env RVC_MODEL_PATH / RVC_INDEX_PATH
    2. /data/models/rvc/*.pth  (volume Docker)
    3. <src>/Voices/*.pth      (bundlé dans l'image pour les tests)
    Retourne ("", "") si aucun modèle trouvé → RVC désactivé.
    """
    # 1. Env vars explicites
    env_model = os.getenv("RVC_MODEL_PATH", "").strip()
    if env_model:
        return env_model, os.getenv("RVC_INDEX_PATH", "").strip()

    # 2. Volume Docker /data/models/rvc/
    try:
        data_rvc = _models_dir() / "rvc"
        if data_rvc.exists():
            pth_files = sorted(data_rvc.glob("*.pth"))
            if pth_files:
                idx_files = sorted(data_rvc.glob("*.index"))
                return str(pth_files[0]), str(idx_files[0]) if idx_files else ""
    except Exception:
        pass

    # 3. Bundlé dans l'image : src/Voices/ (chemin relatif au package)
    voices_dir = Path(__file__).parent.parent.parent / "Voices"
    if voices_dir.exists():
        pth_files = sorted(voices_dir.glob("*.pth"))
        if pth_files:
            idx_files = sorted(voices_dir.glob("*.index"))
            log.info("Modèles RVC trouvés dans %s", voices_dir)
            return str(pth_files[0]), str(idx_files[0]) if idx_files else ""

    return "", ""


def _get_rvc():
    """
    Charge l'instance RVCInference (une seule fois).
    Retourne None si aucun modèle RVC n'est configuré.
    """
    global _rvc_infer
    if _rvc_infer is False:          # sentinelle "déjà tenté, pas de modèle"
        return None
    if _rvc_infer is not None:
        return _rvc_infer

    model_path, index_path = _rvc_paths()
    if not model_path:
        log.info("Aucun modèle RVC trouvé — TTS sans conversion de voix.")
        _rvc_infer = False
        return None

    try:
        from rvc_python.infer import RVCInference  # noqa: PLC0415
        log.info("Chargement du modèle RVC '%s'…", model_path)
        rvc = RVCInference(device="cpu:0")
        rvc.load_model(model_path, index_path=index_path or "")
        _rvc_infer = rvc
        log.info("Modèle RVC chargé.")
        return rvc
    except ImportError:
        log.warning("rvc-python non installé — TTS sans conversion de voix.")
        _rvc_infer = False
        return None
    except Exception as exc:
        log.error("Erreur chargement RVC : %s", exc)
        _rvc_infer = False
        return None


# Paramètres RVC configurables via env vars
_RVC_F0_METHOD  = os.getenv("RVC_F0_METHOD",  "harvest")   # pm | harvest | rmvpe
_RVC_F0_UP_KEY  = int(os.getenv("RVC_F0_UP_KEY",  "0"))    # décalage tonal (demi-tons)
_RVC_INDEX_RATE = float(os.getenv("RVC_INDEX_RATE", "0.75"))


_RVC_SERVICE_URL = os.getenv("RVC_SERVICE_URL", "").rstrip("/")


def _rvc_convert(wav_bytes: bytes) -> bytes:
    """
    Applique la conversion RVC sur un WAV en entrée. Retourne le WAV converti.

    Stratégie (ordre de priorité) :
      1. RVC_SERVICE_URL définie → appel HTTP vers le micro-service dédié
      2. rvc-python installé localement → conversion in-process
      3. Aucun → retourne Piper brut sans conversion
    """
    # ── 1. Service HTTP distant (conteneur mnemo-rvc) ──────────────
    if _RVC_SERVICE_URL:
        import urllib.error
        req = urllib.request.Request(
            f"{_RVC_SERVICE_URL}/convert",
            data=wav_bytes,
            headers={"Content-Type": "audio/wav"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            log.error("RVC service HTTP %s : %s", exc.code, exc.reason)
            return wav_bytes   # fallback Piper brut
        except Exception as exc:
            log.warning("Appel RVC service échoué (%s) — retour Piper brut", exc)
            return wav_bytes

    # ── 2. rvc-python local (fallback hors-conteneur) ──────────────
    rvc = _get_rvc()
    if rvc is None:
        return wav_bytes   # pas de RVC → retourne Piper brut

    in_tmp = out_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            in_tmp = f.name
        out_tmp = in_tmp.replace(".wav", "_rvc.wav")

        rvc.infer_file(
            input_path  = in_tmp,
            output_path = out_tmp,
            f0method    = _RVC_F0_METHOD,
            f0up_key    = _RVC_F0_UP_KEY,
            index_rate  = _RVC_INDEX_RATE,
            filter_radius = 3,
            resample_sr   = 0,
            rms_mix_rate  = 0.25,
            protect       = 0.33,
        )
        with open(out_tmp, "rb") as f:
            return f.read()
    finally:
        for p in (in_tmp, out_tmp):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ══════════════════════════════════════════════════════════════════
# Point d'entrée public
# ══════════════════════════════════════════════════════════════════

def synthesize_speech(text: str) -> bytes:
    """
    Texte → WAV bytes.
    Pipeline : Piper TTS  →  RVC conversion (si modèle disponible)
    """
    piper_wav = _piper_to_wav_bytes(text)
    return _rvc_convert(piper_wav)