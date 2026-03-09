# Mnemo

Agent mémoire personnel — 100% local, 100% privé. Bâti sur [CrewAI](https://crewai.com) + [Ollama](https://ollama.com).

## C'est quoi Mnemo ?

Un assistant personnel qui **se souvient de toi** : conversations, préférences, habitudes, projets. Tout reste sur ta machine, rien n'est envoyé sur internet.

### Mémoire hybride

- **Court terme** : transcription JSON de la session courante
- **Long terme lisible** : `memory.md` — fichier Markdown éditable à la main
- **Long terme indexé** : SQLite avec FTS5 (recherche plein texte) + embeddings 768d (recherche vectorielle)
- **Documents** : ingestion PDF, DOCX, TXT, Markdown, code source

### Fonctionnalités

- Conversation avec mémoire persistante entre les sessions
- Calendrier ICS (Google Calendar, Nextcloud, fichier local) — lecture et écriture
- Recherche web (SearXNG self-hosted ou DuckDuckGo)
- Briefing matinal automatique (`data/briefing.md`)
- Planification en langage naturel (rappels, tâches récurrentes)
- Interface web locale (dashboard React + API FastAPI)
- Ingestion de documents (PDF, DOCX, TXT...)

---

## Installation

Voir [INSTALL.MD](INSTALL.MD) pour le guide complet.

**Résumé en 3 commandes :**

```bash
git clone https://github.com/thurzas/mnemo.git
cd mnemo && chmod +x mnemo.sh install.sh
./mnemo.sh setup
```

**Prérequis** : Docker ≥ 24, Ollama ≥ 0.4

---

## Utilisation

```bash
./mnemo.sh              # Démarre une session de conversation
./mnemo.sh services     # Démarre scheduler + API (daemon)
./mnemo.sh help         # Toutes les commandes disponibles
```

---

## Architecture

8 crews spécialisés orchestrés via CrewAI :

| Crew | Rôle |
|------|------|
| `EvaluationCrew` | Analyse l'intent, route vers le bon crew |
| `ConversationCrew` | Récupère le contexte et génère la réponse |
| `ConsolidationCrew` | Extrait les faits et met à jour `memory.md` |
| `CuriosityCrew` | Détecte les lacunes mémoire, pose des questions |
| `CalendarWriteCrew` | Gestion du calendrier en écriture |
| `ShellCrew` | Exécute des commandes système (whitelist + confirmation) |
| `SchedulerCrew` | Planification en langage naturel |
| `BriefingCrew` | Génère le briefing matinal et le résumé hebdomadaire |

Pour plus de détails, consulte [CLAUDE.md](CLAUDE.md) (architecture complète).

---

## Roadmap

Voir [ROADMAP.md](ROADMAP.md).
