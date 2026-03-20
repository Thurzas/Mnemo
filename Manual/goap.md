# GOAP — Documentation

## Vue d'ensemble

Le moteur GOAP (Goal-Oriented Action Planning) permet à Mnemo de planifier une séquence d'actions pour atteindre un objectif utilisateur. Il est implémenté en **backward chaining** : à partir du but, le planificateur remonte les actions nécessaires jusqu'à atteindre l'état initial.

Le moteur est dans `src/Mnemo/goap/planner.py`.

---

## Moteur GOAP

### `plan(goal, world_state, db_path=None)`

Point d'entrée principal.

| Paramètre | Type | Description |
|-----------|------|-------------|
| `goal` | `str` | Objectif en langage naturel (ex: `"deploy_frontend"`) |
| `world_state` | `dict` | État courant du monde (flags booléens) |
| `db_path` | `str \| None` | Chemin vers le user KG SQLite (optionnel) |

**Retourne :** liste ordonnée d'objets `Action`, du premier au dernier à exécuter.

**Lève :** `PlanningError` si le goal est inatteignable depuis l'état courant.

### Backward chaining

```
goal G
  -> cherche actions dont effect contient G
  -> pour chaque action A :
      -> si préconditions P satisfaites dans world_state : A est applicable
      -> sinon : appel récursif plan(P, world_state)
  -> tri par coût total (somme des coûts des actions)
  -> retourne la séquence de coût minimal
```

### Coûts des actions

| Catégorie | Coût |
|-----------|------|
| LLM / Generate | 5 |
| sandbox_shell, npm, pip, python, file_write | 3 |
| web_search, sandbox_read | 2 |
| memory_read, autres | 1 |

Le planificateur favorise les chemins avec moins d'appels LLM et moins d'actions risquées.

---

## ACTION_REGISTRY

Registre statique des actions disponibles. Chaque entrée décrit :

```python
{
  "name": "install_npm_deps",
  "preconditions": {"package_json_exists": True},
  "effects": {"npm_deps_installed": True},
  "cost": 3,
  "action_type": "sandbox_shell",
  "command_template": "npm install"
}
```

Le registre est enrichi dynamiquement depuis le HP-KG si `db_path` est fourni.

---

## HP-KG (Hybrid Pattern Knowledge Graph)

Le HP-KG stocke les patterns appris sous forme de triplets dans une base SQLite.

### Deux couches

| Couche | Emplacement | Rôle |
|--------|-------------|------|
| **Seed KG** | `src/Mnemo/assets/kg_seed.db` | Patterns génériques curatés, livré avec l'application |
| **User KG** | `users/<username>/memory.db` | Patterns appris des sessions de l'utilisateur |

**Double-lookup :** le user KG est interrogé en premier. Si aucun résultat, le seed KG est utilisé en fallback. Les écritures se font uniquement dans le user KG.

### Schéma SQLite

Tables dans `memory.db` (user KG) :

```sql
-- Nœuds : Actions et États
kg_nodes (
  id        INTEGER PRIMARY KEY,
  type      TEXT,    -- "action" | "state"
  name      TEXT UNIQUE,
  metadata  TEXT     -- JSON
)

-- Arêtes : relations entre nœuds
kg_edges (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER REFERENCES kg_nodes(id),
  target_id   INTEGER REFERENCES kg_nodes(id),
  relation    TEXT,  -- "precondition" | "effect" | "blocks"
  weight      REAL   -- poids appris (fréquence)
)

-- Événements : historique des activations
kg_edge_events (
  id         INTEGER PRIMARY KEY,
  edge_id    INTEGER REFERENCES kg_edges(id),
  session_id TEXT,
  timestamp  TEXT
)
```

### Types de triplets

| Relation | Signification | Exemple |
|----------|---------------|---------|
| `precondition` | Action nécessite cet état | `(install_npm) -[precondition]-> (package_json_exists)` |
| `effect` | Action produit cet état | `(install_npm) -[effect]-> (npm_deps_installed)` |
| `blocks` | Cet état bloque cette action | `(npm_error) -[blocks]-> (run_tests)` |

---

## Intégration avec PlannerCrew

`PlannerCrew` appelle `plan()` pour obtenir la séquence d'actions, puis génère `plan.md` :

```
PlannerCrew.run(goal, recon_context)
  -> plan(goal, current_world_state, db_path=user_memory_db)
  -> [Action1, Action2, Action3]
  -> génère plan.md avec étapes checkboxes
  -> écrit plan.md dans users/<username>/projects/<slug>/
```

Si le planificateur lève `PlanningError`, `PlannerCrew` génère un plan dégradé en langage naturel sans étapes GOAP structurées.

---

## Intégration avec le scheduler (autonomie)

Le scheduler background lit `plan.md` et utilise le HP-KG pour évaluer si l'étape suivante peut être exécutée automatiquement. Voir [scheduler.md](scheduler.md) — section "Couche 3 : Boucle d'autonomie GOAP".

---

## `PlanningError`

Levée quand aucun chemin d'actions ne permet d'atteindre le goal depuis le world_state courant.

**Causes fréquentes :**
- Goal inconnu du `ACTION_REGISTRY` et absent du HP-KG
- Préconditions circulaires (cycle de dépendances)
- État initial incompatible avec toutes les actions disponibles

---

## Variables et configuration

Le moteur GOAP ne lit pas de variables d'environnement. Il est configuré par le code appelant via les paramètres de `plan()`.

Le seed KG (`kg_seed.db`) est embarqué dans l'image Docker. Le user KG est dans le volume `/data` (`users/<username>/memory.db`).
