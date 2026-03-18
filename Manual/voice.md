# Voix — STT / TTS / RVC

## Pipeline complet

```
Micro  →  [STT : faster-whisper]  →  texte  →  Agent
Agent  →  texte  →  [TTS : Kokoro-82M]  →  WAV neutre  →  [RVC]  →  WAV voix custom  →  haut-parleur
```

| Composant | Rôle | Actif |
|-----------|------|-------|
| STT faster-whisper | Parole → texte | Toujours |
| TTS Kokoro-82M | Texte → WAV (FR ou JA) | Toujours |
| RVC micro-service | WAV neutre → WAV voix custom | Optionnel (profil `voice`) |

---

## 1. STT — Transcription vocale (faster-whisper)

### Démarrage

Le bouton micro dans le chat enregistre l'audio et envoie `POST /api/stt`.
La transcription est automatiquement placée dans le champ de saisie.

### Paramètres

| Variable | Défaut | Valeurs possibles |
|----------|--------|-------------------|
| `WHISPER_MODEL` | `tiny` | `tiny` / `base` / `small` / `medium` |

- `tiny` (~39 MB) — rapide, suffisant pour le français courant
- `small` — meilleure qualité, ~4× plus lent
- VAD activé (`vad_filter=True`) — filtre les silences courts
- Langue forcée à `fr` (évite les dérives sur les silences)

### Résolution du modèle

```
1. /app/models/whisper/   ← baked dans l'image au build
2. /data/models/whisper/  ← téléchargé automatiquement au 1er appel
```

---

## 2. TTS — Synthèse vocale (Kokoro-82M)

### Modèle

Kokoro-82M est un modèle TTS multilingue unique (~327 MB, `hexgrad/Kokoro-82M`).
Il remplace Piper depuis la v15 et gère le français et le japonais dans la même instance.

Le modèle est téléchargé automatiquement au premier appel TTS dans `/data/models/`
(volume persistant — pas besoin d'internet après le premier démarrage).

### Détection de langue automatique

Chaque phrase est analysée avant synthèse :
- Contient des hiragana / katakana / kanji → pipeline japonais
- Sinon → pipeline français

Les deux pipelines coexistent en mémoire (singletons lazy, chargés au premier appel).

### Voix disponibles

| Code | Langue | Registre |
|------|--------|----------|
| `ff_siwis` | Français | Féminin, neutre — **seule voix FR disponible dans Kokoro-82M** |
| `jf_alpha` | Japonais | Féminin |
| `jf_nezumi` | Japonais | Féminin |
| `jf_tebukuro` | Japonais | Féminin |
| `jm_kumo` | Japonais | Masculin |

> Pour diversifier la voix française, la personnalisation passe par le modèle RVC.

### Paramètre Vitesse

- `1.0` = vitesse normale
- `0.5–0.8` = plus lent, plus posé
- `1.2–1.8` = plus rapide

Réglable depuis l'onglet *Voix* du dashboard ou via `KOKORO_SPEED` dans `.env`.

### Flux audio par phrases

Le frontend découpe la réponse de l'agent en phrases (`.`, `?`, `!`) avant d'appeler `/api/tts`.
Un seul appel TTS est lancé à la fois, avec prefetch de la phrase suivante pendant la lecture.
Résultat : l'audio commence rapidement et s'enchaîne sans interruption ni superposition.

---

## 3. RVC — Conversion de voix

RVC (Retrieval-based Voice Conversion) transforme le WAV neutre de Kokoro en un WAV
avec le timbre d'une voix cible apprise par un modèle `.pth`.

### Démarrage

```bash
./mnemo.sh rvc          # build + démarre le container RVC
./mnemo.sh logs-rvc     # logs en temps réel
```

Ajouter dans `.env` :
```
RVC_SERVICE_URL=http://mnemo-rvc:7865
```

Sans cette variable, le TTS fonctionne avec la voix Kokoro brute (pas d'erreur).

### Structure d'un modèle

Chaque modèle RVC se compose de deux fichiers :

| Fichier | Rôle | Requis |
|---------|------|--------|
| `nom.pth` | Poids du réseau de conversion | Oui |
| `nom.index` | Index FAISS des features (améliore le timbre) | Non |

Les modèles se placent dans `data/models/rvc/`.

---

## 4. Interface Voix (VoicePage)

L'onglet **Voix** dans le dashboard permet de tout régler sans redémarrer les containers.

### Sections

**Synthèse vocale (Kokoro)**
- Sélecteur de voix française et japonaise
- Slider de vitesse

**Conversion de voix (RVC)**
- Toggle on/off — désactiver pour utiliser Kokoro seul
- Indicateur de connexion au service RVC
- 6 paramètres de conversion (voir section suivante)

**Modèles personnalisés**
- Upload d'un fichier `.pth` (requis) + `.index` (optionnel)
- Bouton *Activer* pour charger le modèle à chaud dans le service RVC
- Sélecteur du modèle actif parmi les modèles uploadés

**Test de la voix**
- Champ texte libre
- Bouton *Tester* : applique les réglages du formulaire **immédiatement** (sans sauvegarder)
  → utile pour ajuster les paramètres en temps réel
- Bouton *Sauvegarder* : persiste les réglages dans `data/voice_settings.json`
  → chargés automatiquement au prochain démarrage

> **Tester ≠ Sauvegarder** : le test active les réglages pour la session en cours.
> Le chat utilisera ces réglages immédiatement, mais ils seront perdus au redémarrage
> si vous ne sauvegardez pas.

### Ajouter un modèle custom

1. Onglet *Voix* → section *Modèles personnalisés*
2. Sélectionner le fichier `.pth` (et optionnellement le `.index` correspondant)
3. Cliquer *Upload* — le fichier est copié dans `data/models/rvc/`
4. Cliquer *Activer* sur le modèle voulu — le service RVC le charge à chaud

---

## 5. Paramètres RVC

### Méthode F0 (`f0_method`)

Algorithme de détection de la hauteur vocale (pitch).

| Valeur | Vitesse | Précision | Usage recommandé |
|--------|---------|-----------|-----------------|
| `harvest` | Lent | Élevée | Voix complexes |
| `pm` | Rapide | Faible | Test rapide |
| `rmvpe` | Rapide | Élevée | **Recommandé** — GPU-accéléré |

### Transposition (`f0_up_key`)

Décale le pitch en demi-tons. Plage : `-12` à `+12`.

- `0` — même hauteur que la voix Kokoro source
- `+12` — une octave plus haut (utile si le modèle est entraîné sur voix masculine)
- `-12` — une octave plus bas

Ajuster si la voix convertie semble trop aiguë ou trop grave par rapport au modèle.

### Index rate (`index_rate`)

Intensité du timbre du dataset d'entraînement. Plage : `0.0` à `1.0`.

- `0.0` — presque aucune conversion, timbre Kokoro brut
- `0.75` — valeur par défaut, bon équilibre
- `1.0` — timbre maximum du modèle, risque d'artefacts si l'index est bruité

Réduire si la voix semble artificielle ou robotique.

### Filter radius (`filter_radius`)

Lissage du pitch par filtre médian. Plage : `0` à `7`.

- `0` — pas de filtre, pitch brut (peut craquer sur les consonnes)
- `3` — lissage modéré, valeur par défaut
- `5–7` — voix très lisse, mais perd les attaques naturelles

### RMS mix rate (`rms_mix_rate`)

Équilibre de volume entre la source et la sortie RVC. Plage : `0.0` à `1.0`.

- `0.0` — volume dicté par le modèle RVC
- `1.0` — volume de la voix Kokoro préservé
- Ajuster si la voix est trop forte ou trop faible après conversion

### Protect (`protect`)

Protection des consonnes et sons non-voisés (s, t, f…). Plage : `0.0` à `0.5`.

- `0.0` — tout converti, consonnes peuvent sonner artificielles
- `0.33` — valeur par défaut, bon équilibre
- `0.5` — consonnes quasi intactes, voix plus naturelle mais conversion moins complète

### Preset de départ

```
f0_method    : rmvpe
f0_up_key    : 0
index_rate   : 0.75
filter_radius: 3
rms_mix_rate : 0.25
protect      : 0.33
```

Ajuster en priorité **index_rate** (timbre) et **f0_up_key** (hauteur) pour les changements
les plus audibles.

---

## 6. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `WHISPER_MODEL` | `tiny` | Modèle STT (`tiny` / `base` / `small` / `medium`) |
| `KOKORO_VOICE_FR` | `ff_siwis` | Voix Kokoro française par défaut |
| `KOKORO_VOICE_JA` | `jf_alpha` | Voix Kokoro japonaise par défaut |
| `KOKORO_SPEED` | `1.0` | Vitesse de synthèse par défaut |
| `RVC_SERVICE_URL` | *(vide)* | URL du container RVC (`http://mnemo-rvc:7865`) |
| `RVC_F0_METHOD` | `harvest` | Méthode F0 par défaut |
| `RVC_F0_UP_KEY` | `0` | Transposition par défaut |
| `RVC_INDEX_RATE` | `0.75` | Index rate par défaut |

Les valeurs `.env` sont les défauts au démarrage. Elles peuvent être surchargées à chaud
depuis l'onglet *Voix* et sont persistées dans `data/voice_settings.json`.
`voice_settings.json` est chargé au démarrage et prend priorité sur les variables d'environnement.

---

## 7. Résilience et fallback

| Situation | Comportement |
|-----------|-------------|
| Service RVC absent ou en timeout | TTS retourne le WAV Kokoro brut — pas d'erreur visible |
| Modèle RVC introuvable | Marqué indisponible, aucun retry, log INFO |
| Kokoro : modèle non encore téléchargé | Téléchargement automatique au premier appel |
| STT : silence ou bruit seul | VAD filtre → retourne chaîne vide `""` |
| Voix japonaise demandée, MeCab absent | Erreur 500 → rebuild image avec `python -m unidic download` |

---

## 8. Docker — points critiques

```yaml
# /tmp doit être exec pour phonemizer (espeak-ng via ctypes)
tmpfs:
  - /tmp:size=256m,mode=1777,exec

# HF_HOME doit pointer vers un volume en écriture
environment:
  - HF_HOME=/data/models
```

Le container principal (`mnemo-api`) a `read_only: true`. Tout ce qui écrit
(modèles HuggingFace, paramètres voix) doit passer par le volume `/data`.

Le container RVC (`mnemo-rvc`) requiert un GPU NVIDIA avec CUDA 12.4.
Sans GPU, laisser `RVC_SERVICE_URL` vide et utiliser Kokoro seul.