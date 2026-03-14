"""
memory_classifier.py — Classifie un message en Bucket A (personnel) ou Bucket B (document).

Bucket A : information courte sur l'utilisateur → memory.md (pipeline NoteWriter)
Bucket B : document de référence, specs, code → doc_chunks (pipeline ingest)

Classifier à 3 niveaux :
  1. Heuristique  (déterministe, < 1 ms)   — taille + structure + intent markers
  2. LLM léger    (1 appel, max_tokens=1)   — si confiance heuristique < THRESHOLD
  3. Fallback     (taille seule)             — si LLM échoue

Utilisé par NoteWriterCrew.run() avant de choisir le pipeline d'écriture.
"""

import os
import re

from dataclasses import dataclass
from typing import Literal

# Import ollama au niveau module pour permettre le mock dans les tests.
# Reste None si ollama n'est pas installé (→ _llm_classify retourne None).
try:
    import ollama as _ollama
except ImportError:
    _ollama = None  # type: ignore[assignment]

# ── Seuils ────────────────────────────────────────────────────────────────────

# Taille approximative en tokens (len(text) // 4) au-dessus de laquelle on
# penche vers Bucket B.
BUCKET_B_TOKEN_THRESHOLD = 300

# Signaux structurels → indicateurs forts de Bucket B
BUCKET_B_STRUCTURE_SIGNALS: list[str] = [
    "```",      # code block
    "## ",      # markdown header niveau 2
    "### ",     # markdown header niveau 3
    "---\n",    # séparateur markdown
    "---\r",    # séparateur markdown (Windows)
]

# Intent markers → Bucket B : l'utilisateur passe du contenu de référence
_INTENT_B: list[str] = [
    "voici ",
    "voilà ",
    "garde ce ",
    "garde ceci",
    "garde ça",
    "ingère",
    "ingere",
    "je vais te passer",
    "je te passe",
    "lis ce",
    "lis ça",
    "analyse ce",
    "analyse ça",
]

# Intent markers → Bucket A : l'utilisateur note un fait personnel
_INTENT_A: list[str] = [
    "note que",
    "notes que",
    "retiens que",
    "retiens bien que",
    "souviens-toi que",
    "souviens toi que",
    "n'oublie pas que",
    "noublie pas que",
    "mémorise que",
    "memorise que",
    "garde en mémoire que",
    "garde en memoire que",
    "enregistre que",
    "important à noter",
    "important a noter",
    "ajoute à ma mémoire",
    "ajoute a ma memoire",
    "écris dans ma mémoire",
    "ecris dans ma memoire",
]

# Seuil de confiance heuristique en dessous duquel on appelle le LLM
CLASSIFIER_CONFIDENCE_THRESHOLD = 0.75


# ── Résultat ──────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    bucket:     Literal["A", "B"]  # A = personnel, B = document
    confidence: float               # 0.0 – 1.0
    method:     str                 # "heuristic" | "llm" | "fallback"
    reason:     str                 # pour le debug log


# ── Niveau 1 : heuristique ────────────────────────────────────────────────────

def _heuristic(text: str) -> ClassificationResult:
    """
    Scoring basé sur des signaux observables sans LLM.

    score_b ∈ [0.0, 1.0] :
      0.0 → certainement Bucket A (fait personnel court)
      1.0 → certainement Bucket B (document structuré)

    confidence = max(score_b, 1 - score_b)  → ∈ [0.5, 1.0]
    """
    reasons: list[str] = []
    score_b: float = 0.0

    text_lower = text.lower()
    token_estimate = len(text) // 4

    # ── Signaux de structure → B ─────────────────────────────────────────────
    # 2+ signaux (ex: headers + code block) → très probablement un document.
    structure_count = sum(1 for sig in BUCKET_B_STRUCTURE_SIGNALS if sig in text)
    if structure_count >= 2:
        score_b += 0.80
        reasons.append(f"{structure_count} signaux structure")
    elif structure_count == 1:
        score_b += 0.45
        reasons.append("1 signal structure")

    # ── Taille → B (par paliers) ─────────────────────────────────────────────
    # Un texte vraiment long est rarement une note personnelle courte.
    if token_estimate >= BUCKET_B_TOKEN_THRESHOLD * 3:    # ≥ 900 tokens
        score_b += 0.75
        reasons.append(f"~{token_estimate} tokens (très long)")
    elif token_estimate >= BUCKET_B_TOKEN_THRESHOLD * 2:  # ≥ 600 tokens
        score_b += 0.60
        reasons.append(f"~{token_estimate} tokens (long)")
    elif token_estimate >= BUCKET_B_TOKEN_THRESHOLD:      # ≥ 300 tokens
        score_b += 0.45
        reasons.append(f"~{token_estimate} tokens")
    elif token_estimate >= BUCKET_B_TOKEN_THRESHOLD // 2: # ≥ 150 tokens
        score_b += 0.15
        reasons.append(f"~{token_estimate} tokens (moyen)")

    # ── Intent markers B → signal fort ───────────────────────────────────────
    # "voici", "ingère", etc. : l'utilisateur passe du contenu de référence.
    for marker in _INTENT_B:
        if marker in text_lower:
            score_b += 0.80
            reasons.append(f"marker B «{marker.strip()}»")
            break  # un seul marker B suffit

    # ── Intent markers A → réduit fortement score_b ──────────────────────────
    for marker in _INTENT_A:
        if marker in text_lower:
            score_b -= 0.55
            reasons.append(f"marker A «{marker}»")
            break  # un seul marker A suffit

    # ── Pronoms personnels → léger signal A ──────────────────────────────────
    if re.search(r"\bje\b|\bmon\b|\bma\b|\bmes\b", text_lower):
        score_b -= 0.15
        reasons.append("pronoms personnels")

    score_b = max(0.0, min(1.0, score_b))

    bucket     = "B" if score_b >= 0.5 else "A"
    confidence = max(score_b, 1.0 - score_b)

    if not reasons:
        reasons.append("texte court sans signaux → A par défaut")

    return ClassificationResult(
        bucket=bucket,
        confidence=confidence,
        method="heuristic",
        reason=", ".join(reasons),
    )


# ── Niveau 2 : LLM léger ─────────────────────────────────────────────────────

def _llm_classify(text: str) -> ClassificationResult | None:
    """
    Appel LLM binaire (A ou B) avec max_tokens=2.
    Retourne None si le LLM est inaccessible ou produit une réponse invalide.

    Utilise _ollama (importé au niveau module) — patchable dans les tests.
    """
    if _ollama is None:
        return None

    model_env = os.getenv("MODEL", "ollama/mistral")
    model     = model_env.removeprefix("ollama/")

    snippet = text[:800].strip()
    prompt  = (
        "Tu dois classifier ce contenu en UNE SEULE LETTRE : A ou B.\n\n"
        "A = information personnelle courte (fait sur l'utilisateur, préférence, "
        "décision personnelle, note de vie).\n"
        "B = document de référence (specs, architecture, code, contenu structuré, "
        "longue liste, fichier de configuration).\n\n"
        f"Contenu :\n{snippet}\n\n"
        "Réponds uniquement par A ou B."
    )

    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 2},
        )
        letter = response["message"]["content"].strip().upper()
        if letter and letter[0] in ("A", "B"):
            letter = letter[0]
            return ClassificationResult(
                bucket=letter,       # type: ignore[arg-type]
                confidence=0.85,
                method="llm",
                reason=f"LLM → {letter}",
            )
    except Exception:
        pass

    return None


# ── API publique ──────────────────────────────────────────────────────────────

def classify_content(user_message: str) -> ClassificationResult:
    """
    Classifie un message en Bucket A (personnel) ou Bucket B (document).

    Niveau 1 : heuristique (taille + structure + intent markers).
    Niveau 2 : LLM léger si confiance heuristique < CLASSIFIER_CONFIDENCE_THRESHOLD.
    Niveau 3 : fallback taille si LLM échoue.
    """
    # ── Niveau 1 ─────────────────────────────────────────────────────────────
    result = _heuristic(user_message)
    if result.confidence >= CLASSIFIER_CONFIDENCE_THRESHOLD:
        return result

    # ── Niveau 2 ─────────────────────────────────────────────────────────────
    llm_result = _llm_classify(user_message)
    if llm_result is not None:
        return llm_result

    # ── Niveau 3 : fallback taille ────────────────────────────────────────────
    token_estimate = len(user_message) // 4
    bucket         = "B" if token_estimate >= BUCKET_B_TOKEN_THRESHOLD else "A"
    return ClassificationResult(
        bucket=bucket,       # type: ignore[arg-type]
        confidence=0.60,
        method="fallback",
        reason=f"fallback taille (~{token_estimate} tokens)",
    )