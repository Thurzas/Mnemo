# Routing — Documentation

## Vue d'ensemble

Quand tu envoies un message à Mnemo, il doit décider ce qu'il doit faire : répondre comme un assistant, exécuter une commande, ajouter un rappel au calendrier, planifier une tâche...

Ce choix s'appelle le **routing**. Il suit une hiérarchie à 3 niveaux, du plus rapide au plus intelligent :

```
1. Mots-clés déterministes    (< 1ms, règles fixes)
      │ si confiance haute
      ▼
2. Classifieur ML              (< 10ms, sklearn TF-IDF)
      │ si ML insuffisant
      ▼
3. LLM EvaluationCrew         (2–5s, analyse sémantique)
```

---

## Niveau 1 — Mots-clés déterministes

Quatre familles de patterns, chacune associée à une route :

| Route | Détecteur | Exemples de triggers |
|-------|-----------|----------------------|
| `shell` | `_detect_shell_intent()` | `"liste les fichiers"`, `"lis le contenu de"`, `"ls /data"` |
| `scheduler` | `_detect_scheduler_intent()` | `"planifie"`, `"programme un rappel"`, `"rappelle-moi"` |
| `note` | `_detect_note_intent()` | `"note que"`, `"mémorise"`, `"souviens-toi"` |
| `calendar` | `_detect_calendar_write_intent()` | `"ajoute au calendrier"`, `"crée un événement"`, `"supprime"` |

**Important :** les keywords `scheduler` ont été resserrés pour éviter les faux positifs. `"programme"` seul ne déclenche plus rien — il faut `"programme un ..."` ou `"programme cette ..."`. Le mot `"programme"` (nom commun = emploi du temps) est correctement routé vers `conversation`.

---

## Niveau 2 — Classifieur ML

Un modèle `TF-IDF → Logistic Regression` entraîné sur `training_data.jsonl`.

**Routes connues du modèle :**
```
calendar | conversation | note | scheduler | shell
```

**Seuils de confiance :**

| Condition | Action |
|-----------|--------|
| Confiance ML ≥ 0.95 | Route ML directement, LLM skippé |
| Confiance ML ≥ 0.80 ET keywords confirment | Route ML directement |
| Confiance ML ≥ 0.40 | ML transmis en conseil au LLM |
| Confiance ML < 0.40 | ML ignoré, LLM seul décide |

**Active learning :** si la confiance est < 0.70, le message est loggé dans `uncertain_cases.jsonl` pour alimenter les futurs ré-entraînements.

**Emplacement du modèle :** `data/router_model.joblib` (priorité) ou `src/Mnemo/router_model.joblib` (fallback baked dans l'image).

---

## Niveau 3 — EvaluationCrew (LLM)

Si les deux premières couches n'ont pas tranché, l'EvaluationCrew analyse sémantiquement le message et produit un JSON de routing :

```json
{
  "route": "conversation",
  "needs_memory": true,
  "needs_calendar": false,
  "needs_web": false,
  "needs_clarification": false,
  "memory_query": "projet phoenix deadline",
  "web_query": null,
  "clarification_question": null
}
```

**Champs importants :**

| Champ | Usage |
|-------|-------|
| `route` | Crew cible : `conversation`, `shell`, `scheduler`, `calendar`, `note` |
| `needs_memory` | Si vrai : le `memory_retriever` cherche dans `memory.md` |
| `needs_calendar` | Si vrai : les événements calendrier sont pré-chargés |
| `needs_web` | Si vrai : demande confirmation utilisateur avant recherche |
| `needs_clarification` | Si vrai : Mnemo pose une question avant de continuer |

---

## Arbitrage ML vs LLM

Quand les deux couches ont un avis :

- **ML ≥ 0.84 ET LLM dit autre chose** → le ML prévaut (comportement configuré)
- **ML < 0.84** → le LLM prévaut

Ce comportement évite que le LLM réinterprète des intentions clairement identifiées par le ML (ex : une commande shell explicite).

---

## Routes et crews cibles

| Route | Crew déclenché | Usage |
|-------|----------------|-------|
| `conversation` | `ConversationCrew` | Réponse normale avec mémoire |
| `shell` | `ShellCrew` | Exécution de commandes (whitelist) |
| `scheduler` | `SchedulerCrew` | Planification de tâches |
| `calendar` | `CalendarWriteCrew` | Ajout/modification/suppression d'événements |
| `note` | `NoteWriterCrew` | Écriture directe dans `memory.md` |

---

## Interceptions avant routing

Certaines conditions interceptent le message avant de le passer au crew cible :

**`needs_clarification`**
→ Mnemo pose une question à l'utilisateur, attend la réponse, puis relance l'EvaluationCrew avec le message original + la clarification.

**`needs_web`**
→ Mnemo affiche la query qui sera envoyée et demande une confirmation explicite. La query est figée par le code — le LLM ne peut pas la modifier après validation.

**`route=shell`**
→ La commande générée est affichée et doit être confirmée explicitement avant exécution.

---

## Ré-entraîner le modèle ML

Le fichier `training_data.jsonl` contient les exemples d'entraînement. Format :
```jsonl
{"text": "peux-tu me donner le programme de mardi ?", "label": "conversation"}
{"text": "lance ls /data", "label": "shell"}
```

Pour ré-entraîner :
```bash
python agent_memory/train_router.py
# Modèle sauvé dans data/router_model.joblib
# Rechargé automatiquement au prochain lancement
```

**À noter :** le script sauvegarde `list(pipeline.classes_)` (ordre alphabétique sklearn) et non l'ordre manuel de la liste `ROUTES`. Ce détail est critique — une inversion des indices de probabilité produirait un classifieur inversé.

---

## Cas particuliers

**`"quel est le programme de mardi ?"`**
→ Keyword `"programme"` seul ne match plus scheduler (trop ambiguë).
→ ML classe à 94% en `conversation`.
→ Route : `ConversationCrew`, qui lit le calendrier.

**`"programme un rappel pour demain 9h"`**
→ Keyword `"programme un "` matche scheduler.
→ Route directe : `SchedulerCrew`.

**`"ls /data/docs"`**
→ Keyword `"ls"` matche shell.
→ Route directe : `ShellCrew` (après confirmation).
