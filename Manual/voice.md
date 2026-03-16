# Voix — TTS + RVC

## Pipeline

```
Texte  →  Kokoro-82M  →  WAV neutre  →  RVC  →  WAV voix custom
```

1. **Kokoro-82M** synthétise le texte en audio neutre (français ou japonais selon le contenu)
2. **RVC** (optionnel) applique une conversion de timbre pour habiller la voix avec un modèle personnalisé

---

## Kokoro-82M

Modèle TTS multilingue unique (~82 MB). Téléchargé automatiquement au premier appel dans `/data/models/`.

### Voix disponibles

| Code | Langue | Registre |
|------|--------|----------|
| `ff_siwis` | Français | Féminin, neutre |
| `jf_alpha` | Japonais | Féminin |
| `jf_nezumi` | Japonais | Féminin |
| `jf_tebukuro` | Japonais | Féminin |
| `jm_kumo` | Japonais | Masculin |

> Kokoro-82M ne fournit qu'une seule voix française (`ff_siwis`). Pour plus de variété côté français, la diversité passe par le modèle RVC.

### Détection de langue

Les phrases contenant des caractères hiragana / katakana / kanji sont automatiquement routées vers le pipeline japonais. Le reste part en français.

### Paramètre Vitesse

- `1.0` = vitesse normale
- `0.5–0.8` = plus lent, plus posé
- `1.2–1.8` = plus rapide

---

## RVC — Conversion de voix

RVC (Retrieval-based Voice Conversion) applique le timbre d'un modèle entraîné sur une voix cible. Il tourne dans un container séparé (`mnemo-rvc`) et communique via HTTP.

### Démarrage

```bash
./mnemo.sh rvc          # build + démarre le container RVC
./mnemo.sh logs-rvc     # voir les logs en temps réel
```

Ajouter dans `.env` :
```
RVC_SERVICE_URL=http://mnemo-rvc:7865
```

### Modèles

Les modèles sont stockés dans `data/models/rvc/`. Chaque modèle se compose de :
- **`nom.pth`** — poids du modèle (requis)
- **`nom.index`** — index de features pour la conversion (optionnel, améliore la qualité)

**Via l'UI** : onglet *Voix* → section *Ajouter un modèle* → upload `.pth` + `.index` optionnel → bouton *Activer* pour charger à chaud.

**Via le filesystem** : déposer les fichiers dans `data/models/rvc/` et redémarrer le container RVC.

---

## Paramètres RVC

### Méthode F0 (`f0_method`)

Algorithme de détection de la hauteur vocale (pitch).

| Valeur | Vitesse | Précision | Usage recommandé |
|--------|---------|-----------|-----------------|
| `harvest` | Lent | Élevée | Voix complexes |
| `pm` | Rapide | Faible | Test rapide |
| `rmvpe` | Rapide | Élevée | **Recommandé** |

### Transposition (`f0_up_key`)

Décale le pitch en demi-tons. Plage : `-12` à `+12`.

- `0` — même hauteur que la voix Kokoro source
- `+12` — une octave plus haut (utile si le modèle est entraîné sur voix masculine)
- `-12` — une octave plus bas

Ajuster si la voix convertie semble trop aiguë ou trop grave par rapport au modèle d'entraînement.

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

---

## Preset de départ

```
f0_method    : rmvpe
f0_up_key    : 0
index_rate   : 0.75
filter_radius: 3
rms_mix_rate : 0.25
protect      : 0.33
```

Ajuster en priorité **index_rate** (timbre) et **f0_up_key** (hauteur) pour les changements les plus audibles.

---

## Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `RVC_SERVICE_URL` | *(vide)* | URL du container RVC (`http://mnemo-rvc:7865`) |
| `RVC_F0_METHOD` | `harvest` | Méthode F0 par défaut |
| `RVC_F0_UP_KEY` | `0` | Transposition par défaut |
| `RVC_INDEX_RATE` | `0.75` | Index rate par défaut |
| `KOKORO_VOICE_FR` | `ff_siwis` | Voix Kokoro française par défaut |
| `KOKORO_VOICE_JA` | `jf_alpha` | Voix Kokoro japonaise par défaut |
| `KOKORO_SPEED` | `1.0` | Vitesse de synthèse par défaut |

Les valeurs en `.env` sont les défauts au démarrage. Elles peuvent être surchargées à chaud depuis l'UI (onglet *Voix*) et sont persistées dans `data/voice_settings.json`.