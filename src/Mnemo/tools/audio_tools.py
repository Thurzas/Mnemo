"""
audio_tools.py — STT (Whisper) + TTS (Kokoro → RVC) pour Mnemo

Pipeline :
  STT : audio  →  faster-whisper  →  texte
  TTS : texte  →  Kokoro-82M (voix neutre, FR ou JA)  →  RVC (conversion voix custom)  →  audio

Modèles :
  Whisper  : tiny (~39 MB), téléchargé dans /data/models/whisper/ au 1er usage
  Kokoro   : ~82 MB total, téléchargé automatiquement par le package au 1er usage
             Langues supportées : fr, ja, en, ko, zh, es, pt, hi, it (un seul modèle)
  RVC      : résolution automatique (voir _rvc_paths())

Résolution des fichiers RVC (ordre de priorité) :
  1. Variables d'env RVC_MODEL_PATH + RVC_INDEX_PATH (chemin explicite)
  2. /data/models/rvc/*.pth + *.index  (volume Docker persistant)
  3. <package>/../../Voices/*.pth + *.index  (bundlé dans l'image, pour les tests)

Dépendances optionnelles :
  pip install faster-whisper kokoro rvc-python
  pip install misaki[ja]          # phonémisation japonaise
  apt-get install espeak-ng       # phonémisation française (Linux / Docker)
"""
from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import threading
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# ── Lazy singletons ────────────────────────────────────────────────
_whisper_model       = None
_kokoro_pipeline_fr  = None   # KPipeline(lang_code='f') — français
_kokoro_pipeline_ja  = None   # KPipeline(lang_code='j') — japonais
_rvc_infer           = None   # RVCInference instance, None si non configuré

# Verrous pour l'initialisation thread-safe des singletons Kokoro.
# Le frontend envoie toutes les phrases TTS en parallèle → plusieurs threads
# peuvent appeler _get_kokoro_*() simultanément ; sans lock chacun instancierait
# le modèle séparément (gaspillage mémoire + écriture concurrente dans HF cache).
_kokoro_fr_lock = threading.Lock()
_kokoro_ja_lock = threading.Lock()


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
# TTS étape 1 — Kokoro-82M (texte → WAV neutre, multilingue)
# ══════════════════════════════════════════════════════════════════
#
# Voix disponibles :
#   Français  : ff_siwis (féminine, neutre)  ff_emma  fm_galvani (masculine)
#   Japonais  : jf_alpha  jf_nezumi  jf_tebukuro  jm_kumo (masculine)
#   Anglais   : af_heart  af_bella  am_adam  ...
#
# Kokoro télécharge automatiquement ses poids (~82 MB) au 1er appel.
# Toutes les voix partagent le même modèle.

_KOKORO_VOICE_FR    = os.getenv("KOKORO_VOICE_FR",  "ff_siwis")
_KOKORO_VOICE_JA    = os.getenv("KOKORO_VOICE_JA",  "jf_alpha")
_KOKORO_SPEED       = float(os.getenv("KOKORO_SPEED", "1.0"))
_KOKORO_REPO_ID     = "hexgrad/Kokoro-82M"
_KOKORO_SAMPLE_RATE = 24_000   # constant — toutes les voix Kokoro sortent en 24 kHz

# Voix disponibles (exposées à l'UI)
KOKORO_VOICES_FR = ["ff_siwis", "ff_emma", "fm_galvani"]
KOKORO_VOICES_JA = ["jf_alpha", "jf_nezumi", "jf_tebukuro", "jm_kumo"]

# ── Paramètres runtime (modifiables depuis l'UI sans redémarrer) ────
# Surchargent les valeurs env ; persistés dans /data/voice_settings.json.
_runtime_settings: dict = {}
_settings_lock = threading.Lock()


def get_voice_settings() -> dict:
    """Retourne les paramètres voix effectifs (runtime > env defaults)."""
    with _settings_lock:
        return {
            "rvc_enabled":       _runtime_settings.get("rvc_enabled",       True),
            "kokoro_voice_fr":   _runtime_settings.get("kokoro_voice_fr",   _KOKORO_VOICE_FR),
            "kokoro_voice_ja":   _runtime_settings.get("kokoro_voice_ja",   _KOKORO_VOICE_JA),
            "kokoro_speed":      _runtime_settings.get("kokoro_speed",      _KOKORO_SPEED),
            # RVC — lit les env vars directement pour éviter la dépendance à l'ordre de définition
            "rvc_f0_method":     _runtime_settings.get("rvc_f0_method",     os.getenv("RVC_F0_METHOD",  "harvest")),
            "rvc_f0_up_key":     _runtime_settings.get("rvc_f0_up_key",     int(os.getenv("RVC_F0_UP_KEY",  "0"))),
            "rvc_index_rate":    _runtime_settings.get("rvc_index_rate",    float(os.getenv("RVC_INDEX_RATE", "0.75"))),
            "rvc_filter_radius": _runtime_settings.get("rvc_filter_radius", 3),
            "rvc_rms_mix_rate":  _runtime_settings.get("rvc_rms_mix_rate",  0.25),
            "rvc_protect":       _runtime_settings.get("rvc_protect",       0.33),
        }


def apply_voice_settings(settings: dict) -> None:
    """Met à jour les paramètres runtime (appelé depuis l'API)."""
    with _settings_lock:
        _runtime_settings.update(settings)


def _get_kokoro_fr():
    """Pipeline Kokoro français (lazy singleton, thread-safe)."""
    global _kokoro_pipeline_fr
    if _kokoro_pipeline_fr is None:
        with _kokoro_fr_lock:
            if _kokoro_pipeline_fr is None:   # double-check après acquisition du lock
                from kokoro import KPipeline  # noqa: PLC0415
                log.info("Chargement du pipeline Kokoro FR…")
                _kokoro_pipeline_fr = KPipeline(lang_code="f", repo_id=_KOKORO_REPO_ID)
                log.info("Pipeline Kokoro FR chargé.")
    return _kokoro_pipeline_fr


def _get_kokoro_ja():
    """Pipeline Kokoro japonais (lazy singleton, thread-safe)."""
    global _kokoro_pipeline_ja
    if _kokoro_pipeline_ja is None:
        with _kokoro_ja_lock:
            if _kokoro_pipeline_ja is None:   # double-check après acquisition du lock
                from kokoro import KPipeline  # noqa: PLC0415
                log.info("Chargement du pipeline Kokoro JA…")
                _kokoro_pipeline_ja = KPipeline(lang_code="j", repo_id=_KOKORO_REPO_ID)
                log.info("Pipeline Kokoro JA chargé.")
    return _kokoro_pipeline_ja


def _contains_japanese(text: str) -> bool:
    """True si le texte contient des caractères hiragana, katakana ou kanji."""
    for ch in text:
        cp = ord(ch)
        if (0x3040 <= cp <= 0x30FF    # hiragana + katakana
                or 0x4E00 <= cp <= 0x9FFF    # CJK unifié (kanji communs)
                or 0xFF65 <= cp <= 0xFF9F):   # katakana demi-chasse
            return True
    return False


def _kokoro_to_wav_bytes(text: str, pipeline, voice: str) -> bytes:
    """
    Kokoro pipeline → WAV bytes (PCM 16-bit mono 24 000 Hz).

    Kokoro retourne un générateur de (graphèmes, phonèmes, audio_np).
    Les segments audio sont concaténés puis convertis en WAV.
    """
    import wave
    import numpy as np

    parts: list[np.ndarray] = []
    for _gs, _ps, audio in pipeline(text, voice=voice, speed=_KOKORO_SPEED):
        if audio is not None and len(audio) > 0:
            # Kokoro retourne des torch.Tensor — convertir en numpy pour wave/numpy ops
            if hasattr(audio, "numpy"):
                audio = audio.numpy()
            parts.append(audio)

    if not parts:
        return b""

    combined = np.concatenate(parts) if len(parts) > 1 else parts[0]
    # float32 [-1, 1] → int16
    pcm = (combined * 32_767).clip(-32_768, 32_767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_KOKORO_SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
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
      1. RVC_SERVICE_URL définie → appel HTTP avec params en query string
      2. rvc-python installé localement → conversion in-process
      3. Aucun → retourne le WAV Kokoro brut sans conversion
    """
    s = get_voice_settings()

    # ── 1. Service HTTP distant (conteneur mnemo-rvc) ──────────────
    if _RVC_SERVICE_URL:
        import urllib.error
        qs = (
            f"?f0_method={s['rvc_f0_method']}"
            f"&f0_up_key={s['rvc_f0_up_key']}"
            f"&index_rate={s['rvc_index_rate']}"
            f"&filter_radius={s['rvc_filter_radius']}"
            f"&rms_mix_rate={s['rvc_rms_mix_rate']}"
            f"&protect={s['rvc_protect']}"
        )
        req = urllib.request.Request(
            f"{_RVC_SERVICE_URL}/convert{qs}",
            data=wav_bytes,
            headers={"Content-Type": "audio/wav"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            log.error("RVC service HTTP %s : %s", exc.code, exc.reason)
            return wav_bytes   # fallback Kokoro brut
        except Exception as exc:
            log.warning("Appel RVC service échoué (%s) — retour Kokoro brut", exc)
            return wav_bytes

    # ── 2. rvc-python local (fallback hors-conteneur) ──────────────
    rvc = _get_rvc()
    if rvc is None:
        return wav_bytes   # pas de RVC → retourne Kokoro brut

    in_tmp = out_tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            in_tmp = f.name
        out_tmp = in_tmp.replace(".wav", "_rvc.wav")

        rvc.infer_file(
            input_path    = in_tmp,
            output_path   = out_tmp,
            f0method      = s["rvc_f0_method"],
            f0up_key      = s["rvc_f0_up_key"],
            index_rate    = s["rvc_index_rate"],
            filter_radius = s["rvc_filter_radius"],
            resample_sr   = 0,
            rms_mix_rate  = s["rvc_rms_mix_rate"],
            protect       = s["rvc_protect"],
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

# Ponctuation de fin de phrase : FR + JP
_SENTENCE_END = re.compile(r'(?<=[.!?;。！？])\s+')


def _split_into_chunks(text: str) -> list[str]:
    """
    Découpe le texte en unités synthétisables.

    Stratégie :
    1. Split par ligne (les items numérotés, les sauts de paragraphe)
    2. Split intra-ligne sur la ponctuation de fin de phrase
    Filtre les chunks vides.
    """
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = _SENTENCE_END.split(line)
        chunks.extend(p.strip() for p in parts if p.strip())
    return chunks


def _resample_pcm(frames: bytes, src_rate: int, dst_rate: int, sampwidth: int = 2) -> bytes:
    """
    Rééchantillonne des frames PCM 16-bit mono de src_rate vers dst_rate.
    Utilise numpy (déjà en dépendance du projet).
    Fallback : retourne les frames non modifiées si numpy n'est pas disponible.
    """
    if src_rate == dst_rate:
        return frames
    try:
        import numpy as np
        pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        ratio = dst_rate / src_rate
        new_len = int(len(pcm) * ratio)
        # Rééchantillonnage linéaire — suffisant pour de la voix
        indices = np.linspace(0, len(pcm) - 1, new_len)
        resampled = np.interp(indices, np.arange(len(pcm)), pcm).astype(np.int16)
        return resampled.tobytes()
    except ImportError:
        log.warning("numpy absent — rééchantillonnage impossible, audio peut être désynchronisé")
        return frames


def _concat_wavs(wav_parts: list[bytes]) -> bytes:
    """
    Concatène plusieurs WAV PCM en un seul.

    Le format de référence est celui du premier chunk valide.
    Les chunks avec un sample rate différent sont rééchantillonnés (numpy).
    Les chunks avec channels ou sampwidth différents sont ignorés.
    """
    import wave

    all_frames = b""
    ref: tuple[int, int, int] | None = None  # (channels, sampwidth, framerate)

    for wav_bytes in wav_parts:
        try:
            buf = io.BytesIO(wav_bytes)
            with wave.open(buf, "rb") as wf:
                ch, sw, fr = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
                frames = wf.readframes(wf.getnframes())
                if ref is None:
                    ref = (ch, sw, fr)
                if ch != ref[0] or sw != ref[1]:
                    log.warning(
                        "Chunk WAV incompatible (channels/sampwidth), ignoré : ch=%d sw=%d", ch, sw
                    )
                    continue
                if fr != ref[2]:
                    frames = _resample_pcm(frames, fr, ref[2], sw)
                all_frames += frames
        except Exception as exc:
            log.warning("Impossible de lire un chunk WAV : %s", exc)

    if not all_frames or ref is None:
        return wav_parts[0] if wav_parts else b""

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(ref[0])
        wf.setsampwidth(ref[1])
        wf.setframerate(ref[2])
        wf.writeframes(all_frames)
    return out.getvalue()


def synthesize_speech(text: str) -> bytes:
    """
    Texte → WAV bytes.

    Le texte est découpé en phrases. Chaque phrase est synthétisée
    indépendamment avec la voix Kokoro appropriée :
    - Phrase contenant du japonais → pipeline JA (jf_alpha), RVC ignoré
      (le modèle RVC est entraîné sur voix FR — le japonais y produirait des artefacts)
    - Phrase en français (ou autre) → pipeline FR (ff_siwis) → RVC

    Tous les WAVs Kokoro sortent à 24 000 Hz — pas de rééchantillonnage nécessaire
    entre les chunks FR et JA (sauf si RVC change le sample rate en sortie).
    """
    s = get_voice_settings()
    chunks = _split_into_chunks(text)
    if not chunks:
        return b""

    wav_parts: list[bytes] = []
    for chunk in chunks:
        if _contains_japanese(chunk):
            wav = _kokoro_to_wav_bytes(chunk, _get_kokoro_ja(), s["kokoro_voice_ja"])
        else:
            raw = _kokoro_to_wav_bytes(chunk, _get_kokoro_fr(), s["kokoro_voice_fr"])
            wav = _rvc_convert(raw) if s["rvc_enabled"] else raw
        if wav:
            wav_parts.append(wav)

    if not wav_parts:
        return b""
    if len(wav_parts) == 1:
        return wav_parts[0]

    return _concat_wavs(wav_parts)