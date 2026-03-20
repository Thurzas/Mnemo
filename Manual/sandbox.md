# Sandbox — Documentation

## Vue d'ensemble

Le système sandbox permet à Mnemo de travailler sur des projets de développement isolés. Chaque projet dispose de son propre répertoire, d'un dépôt git auto-initialisé, d'un plan GOAP et d'une mémoire locale.

L'orchestration du travail dans un projet est assurée par `SandboxCrew`. Le cycle de vie est piloté par le scheduler background (autonomie GOAP) et les confirmations utilisateur.

---

## Structure d'un projet

```
users/<username>/projects/<slug>/
  project.json      ← manifest du projet
  plan.md           ← plan GOAP (étapes checkboxes)
  memory.md         ← mémoire locale au projet
  logs/
    commands.log    ← sorties des commandes shell exécutées
  .git/             ← dépôt git auto-initialisé à la création
```

### `project.json`

```json
{
  "slug": "waifuclawd",
  "name": "Waifuclawd",
  "goal": "Implémenter le système de sandbox",
  "status": "in_progress",
  "created_at": "2026-03-20T10:00:00"
}
```

| Champ | Valeurs possibles |
|-------|-------------------|
| `status` | `pending` \| `in_progress` \| `done` \| `error` |

---

## API REST

Toutes les routes sont préfixées par `/api/projects`.

### Projets

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/projects` | Liste tous les projets de l'utilisateur |
| `POST` | `/api/projects` | Crée un nouveau projet (génère slug, init git, crée plan via PlannerCrew) |
| `GET` | `/api/projects/{slug}` | Détails du projet + liste des fichiers |
| `DELETE` | `/api/projects/{slug}` | Supprime le projet et son répertoire |

**Body `POST /api/projects` :**
```json
{
  "name": "Mon Projet",
  "goal": "Implémenter une feature X"
}
```

### Fichiers

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/projects/{slug}/file?path=plan.md` | Lire un fichier du projet |
| `POST` | `/api/projects/{slug}/file` | Écrire un fichier (commit git automatique) |

**Body `POST /api/projects/{slug}/file` :**
```json
{
  "path": "src/feature.py",
  "content": "# code ici"
}
```

### Git

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/projects/{slug}/git` | Log git (20 derniers commits) |

### Confirmations

| Méthode | Route | Description |
|---------|-------|-------------|
| `GET` | `/api/confirmations` | Liste toutes les actions en attente de validation |
| `POST` | `/api/confirmations/{id}` | Approuver ou rejeter une action |

**Body `POST /api/confirmations/{id}` :**
```json
{ "approved": true }
```

Si `approved=true`, la commande est exécutée dans le répertoire du projet et le résultat est loggé dans `logs/commands.log`. Si `approved=false`, l'entrée est supprimée de `pending_confirmations` sans exécution.

---

## Cycle de vie d'un projet

```
POST /api/projects
  -> création répertoire + project.json
  -> git init
  -> PlannerCrew génère plan.md
  -> status = "in_progress"

scheduler _goap_autonomy_tick() (toutes les 60s)
  -> lit plan.md, trouve première étape non cochée
  -> _advance_project() :
      -> action non-risquée -> exécution automatique
      -> action risquée     -> pending_confirmations
  -> si toutes étapes cochées -> status = "done"

utilisateur approuve via dashboard
  -> POST /api/confirmations/{id} {approved: true}
  -> commande exécutée
  -> étape cochée dans plan.md
  -> git commit automatique
```

---

## Confirmations — détail

### Format d'une entrée `pending_confirmations`

```json
{
  "id": "proj_waifuclawd_step_2_1711234567",
  "project_slug": "waifuclawd",
  "step": "Installer les dépendances npm",
  "action": "sandbox_shell",
  "command": "npm install",
  "created_at": "2026-03-20T10:30:00"
}
```

### Actions qui requièrent confirmation

| Action GOAP | Commande typique | Risque |
|-------------|-----------------|--------|
| `sandbox_shell` | commandes shell arbitraires | Modification filesystem |
| `npm` | `npm install`, `npm run build` | Téléchargement packages |
| `pip` | `pip install <pkg>` | Téléchargement packages |
| `python` | `python script.py` | Exécution de code |
| `file_write` | écriture de fichiers | Modification fichiers |

### Actions exécutées automatiquement (sans confirmation)

| Action GOAP | Description |
|-------------|-------------|
| `sandbox_read` | Lecture de fichiers du projet |
| `web_search` | Recherche web (soumise à confirmation séparée si `pending_web_search`) |
| `memory_read` | Lecture de la mémoire locale du projet |

---

## Dashboard — panneau "Actions en attente"

Le dashboard React (page `ProjectsPage`) affiche un panneau dédié aux confirmations en attente :

- Polling toutes les 30 secondes sur `GET /api/confirmations`
- Pour chaque action : description de l'étape, commande à exécuter, projet concerné
- Boutons **Approuver** / **Rejeter**
- Après approbation : output de la commande affiché dans le terminal intégré

---

## Sécurité et isolation

- Toutes les opérations fichier sont confinées au répertoire `users/<username>/projects/<slug>/`
- Les commandes shell passent par la même whitelist que `ShellCrew` (voir [crews.md](crews.md))
- Un git commit automatique est créé après chaque écriture de fichier approuvée, permettant de revenir en arrière
- Les commandes avec timeout dépassé (30s) sont interrompues et loggées comme erreur

---

## Variables d'environnement

Le système sandbox ne requiert pas de variables d'environnement spécifiques. Il utilise `DATA_PATH` (défaut `/data`) comme racine pour `users/<username>/projects/`.
