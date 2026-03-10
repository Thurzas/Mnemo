# Scheduler — Capacités, limites et tests utilisateur

## Ce que le scheduler peut faire

### Actions disponibles

| Action | Déclenchement | Résultat |
|--------|--------------|---------|
| `reminder` | one_shot ou recurring | Injecte un bloc `## Rappel` dans `briefing.md` |
| `briefing` | one_shot ou recurring | Génère `briefing.md` complet (calendrier + mémoire + session) |
| `weekly` | one_shot ou recurring | Génère `weekly.md` (résumé semaine passée) |
| `deadline_alert` | one_shot ou recurring | Scanne le calendrier J-1/J-3 et injecte dans `briefing.md` |

### Fréquences supportées

| Format | Exemple de demande | cron_expr généré |
|--------|-------------------|------------------|
| Quotidien à heure fixe | "tous les jours à 7h30" | `daily 07:30` |
| Hebdomadaire | "chaque lundi à 8h" | `weekly lundi 08:00` |
| Unique à date précise | "demain à 9h" | `trigger_at` ISO calculé |
| Unique dans N jours | "dans 3 jours à 10h" | `trigger_at` ISO calculé |

### Décomposition multi-tâches

Une demande complexe est automatiquement décomposée en primitives :

> "tous les lundis à 8h, briefing + rappelle-moi de faire mon point projets"
> → tâche `briefing` (weekly lundi 08:00) + tâche `reminder` (weekly lundi 08:00)

### Annulation

> "annule le rappel de point projets"
> → identifie la tâche dans `tasks.md`, retourne `action=cancel`

---

## Ce que le scheduler ne peut PAS faire

| Limitation | Comportement |
|------------|-------------|
| Fréquence bimensuelle ("toutes les 2 semaines") | Refus explicite + suggestion quotidien/hebdo |
| Mensuel ("le 1er de chaque mois") | Refus explicite + suggestion quotidien/hebdo |
| Sub-journalier récurrent ("toutes les 2 heures") | Refus explicite |
| Jours ouvrés ("lundi au vendredi") | Crée 5 tâches weekly distinctes (lun/mar/mer/jeu/ven) |
| Modification d'une tâche | Décomposé automatiquement en cancel + create |
| Pas de notification push | Les résultats vont dans `briefing.md` uniquement |
| Exécution dépend du service Docker | Si `mnemo-scheduler` est éteint, les tâches ne s'exécutent pas |
| Pas d'action custom | Impossible de planifier une commande shell ou une ingestion |

---

## Protocole de test utilisateur

Les tests sont organisés du plus simple au plus complexe. Pour chaque cas, noter le résultat et l'éventuel écart.

> **Vérifier après chaque test :** `cat data/tasks.md`
> **Vérifier l'exécution :** `cat data/briefing.md`

---

### Bloc 1 — Rappels one-shot (cas nominaux)

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 1.1 | `rappelle-moi demain à 9h de relire mes notes` | tâche one_shot reminder, trigger_at = demain 09:00 |
| 1.2 | `dans 3 jours à 14h, pense à appeler le médecin` | tâche one_shot reminder, trigger_at = +3j 14:00 |
| 1.3 | `ce soir à 20h, rappelle-moi de fermer le garage` | tâche one_shot reminder, trigger_at = aujourd'hui 20:00 |
| 1.4 | `vendredi à 18h, rappel départ en weekend` | tâche one_shot reminder, trigger_at = vendredi prochain 18:00 |

---

### Bloc 2 — Rappels récurrents

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 2.1 | `tous les jours à 7h30, rappelle-moi de prendre mes médicaments` | recurring reminder, cron `daily 07:30` |
| 2.2 | `chaque lundi matin à 9h, rappelle-moi de faire mon point projets` | recurring reminder, cron `weekly lundi 09:00` |
| 2.3 | `chaque vendredi soir, rappelle-moi de ranger mon bureau` | recurring reminder, cron `weekly vendredi` + heure par défaut |
| 2.4 | `tous les matins, envoie-moi un briefing` | recurring briefing, cron `daily` + heure par défaut (07:30 ?) |

---

### Bloc 3 — Actions avancées (briefing, weekly)

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 3.1 | `génère un briefing tous les matins à 7h` | recurring briefing, cron `daily 07:00` |
| 3.2 | `chaque lundi à 8h, génère le résumé de la semaine` | recurring weekly, cron `weekly lundi 08:00` |
| 3.3 | `maintenant, génère le briefing du jour` | one_shot briefing, trigger_at ≈ maintenant |
| 3.4 | `active les alertes de deadline chaque matin à 7h` | recurring deadline_alert, cron `daily 07:00` |

---

### Bloc 4 — Décomposition multi-tâches

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 4.1 | `tous les lundis à 8h : briefing + rappel de faire mon point projets` | 2 tâches : briefing + reminder, même cron |
| 4.2 | `chaque matin à 7h : briefing, scan des deadlines et rappel de boire de l'eau` | 3 tâches : briefing + deadline_alert + reminder |
| 4.3 | `demain à 9h, rappelle-moi du RDV dentiste. Et chaque vendredi, rappel de faire ma note de frais` | 2 tâches : one_shot + recurring |

---

### Bloc 5 — Annulation

> **Prérequis :** avoir au moins 2 tâches créées (ex: blocs 2.1 et 2.2)

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 5.1 | `annule le rappel médicaments` | cancel de la tâche daily 07:30 médicaments |
| 5.2 | `supprime le rappel du point projets du lundi` | cancel de la tâche weekly lundi point projets |
| 5.3 | `annule tous les rappels` | cancel de toutes les tâches pending *(cas limite)* |
| 5.4 | `annule le rappel de demain` *(sans tâche correspondante)* | message "tâche introuvable" ou demande de clarification |

---

### Bloc 6 — Cas ambigus (defaults automatiques)

Le prompt gère désormais ces cas avec des valeurs par défaut explicites et un signalement obligatoire.

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 6.1 | `rappelle-moi de prendre mes médicaments` *(sans heure)* | one_shot demain 09:00 + confirmation "J'ai supposé demain à 9h" |
| 6.2 | `rappelle-moi à 15h de prendre mes médicaments` *(heure future)* | one_shot **aujourd'hui** 15:00 + confirmation "J'ai planifié pour aujourd'hui à 15h" |
| 6.3 | `rappelle-moi demain` *(sans contenu)* | one_shot demain 09:00, payload.message = résumé de l'intent + signalement |
| 6.4 | `tous les lundis, rappel` *(message non précisé)* | recurring weekly lundi 09:00, payload.message extrait de l'intent |
| 6.5 | `chaque semaine, briefing` *(pas de jour)* | recurring weekly lundi (défaut) + confirmation "J'ai supposé le lundi" |

---

### Bloc 7 — Fréquences non supportées et modification

Le prompt gère ces cas avec des comportements définis (refus explicite ou cancel+create).

| # | Message à envoyer | Attendu |
|---|-------------------|---------|
| 7.1 | `toutes les 2 semaines, rappel réunion` | tasks=[] + message "Je ne peux planifier qu'en quotidien ou hebdomadaire" |
| 7.2 | `le 1er de chaque mois, rappel loyer` | tasks=[] + message expliquant la limitation mensuelle |
| 7.3 | `tous les jours ouvrés à 8h, standup` | 5 tâches weekly (lun/mar/mer/jeu/ven 08:00) + confirmation "lun–ven" |
| 7.4 | `toutes les 2 heures, rappel de faire une pause` | tasks=[] + message "fréquence sous-journalière non supportée" |
| 7.5 | `modifie le rappel médicaments pour 8h au lieu de 7h30` | cancel ancienne tâche + create nouvelle à 08:00 |

---

## Grille de résultats

| # | Message testé | Résultat observé | Statut | Notes |
|---|---------------|-----------------|--------|-------|
| 1.1 | | | | |
| 1.2 | | | | |
| 1.3 | | | | |
| 1.4 | | | | |
| 2.1 | | | | |
| 2.2 | | | | |
| 2.3 | | | | |
| 2.4 | | | | |
| 3.1 | | | | |
| 3.2 | | | | |
| 3.3 | | | | |
| 3.4 | | | | |
| 4.1 | | | | |
| 4.2 | | | | |
| 4.3 | | | | |
| 5.1 | | | | |
| 5.2 | | | | |
| 5.3 | | | | |
| 5.4 | | | | |
| 6.1 | | | | |
| 6.2 | | | | |
| 6.3 | | | | |
| 6.4 | | | | |
| 6.5 | | | | |
| 7.1 | | | | |
| 7.2 | | | | |
| 7.3 | | | | |
| 7.4 | | | | |
| 7.5 | | | | |
