# Calendrier — Documentation

## Vue d'ensemble

Mnemo intègre ton agenda de deux façons :

1. **Lecture** — contexte temporel injecté dans toutes les réponses (événements du jour, agenda 7 jours, deadlines)
2. **Écriture** — création, modification et suppression d'événements via le chat

**Source supportée :** fichier `.ics` local ou URL iCal (Google Calendar, Nextcloud, Radicale).

---

## Configuration

Dans `.env` :

```bash
# Fichier ICS local (chemin DANS le container, donc sous /data)
CALENDAR_SOURCE=/data/agenda.ics

# Google Calendar (lien secret iCal)
CALENDAR_SOURCE=https://calendar.google.com/calendar/ical/xxx/basic.ics

# Nextcloud / Radicale
CALENDAR_SOURCE=https://nextcloud.local/remote.php/dav/calendars/user/default/

# Fenêtre de visibilité (défaut : 14 jours)
CALENDAR_LOOKAHEAD_DAYS=14
```

Laisser `CALENDAR_SOURCE=` vide désactive le calendrier. Mnemo fonctionne sans.

---

## Lecture — Contexte temporel

À chaque message, Mnemo injecte automatiquement un **bloc temporel** dans le prompt de tous les agents :

```
Aujourd'hui : lundi 9 mars 2026 | Semaine 11
Semaine en cours : lun 9 mars → dim 15 mars
Hier : dimanche 8 mars 2026

Agenda - 7 prochains jours :
  lundi 9 mars   [09:00] Standup équipe (30 min)
  mercredi 11 mars [14:00] Réunion client Acme (2h)
  vendredi 13 mars [Journée] Offsite équipe

Deadlines et événements proches :
  ⚠ Demain : Réunion client Acme (dans 2 jours)
```

Ce bloc est la **source primaire** pour toutes les questions calendrier. Mnemo ne cherche pas dans `memory.md` pour répondre à "quel est mon programme de mercredi" — il lit ce bloc directement.

**Cache :** le calendrier est mis en cache 5 minutes en mémoire. Pas d'appel réseau ou disque à chaque message.

---

## Lecture — Expansion des récurrences

Mnemo gère les événements récurrents `RRULE` :

- `WEEKLY`, `DAILY` avec `BYDAY`, `COUNT`, `UNTIL`
- Exceptions (`EXDATE`) correctement exclues
- Bibliothèque `recurring_ical_events` si disponible, expansion manuelle sinon

---

## Écriture — Modifier le calendrier par le chat

Tu peux créer, modifier ou supprimer des événements directement dans la conversation :

```
"Ajoute un rendez-vous chez le dentiste vendredi à 10h"
"Décale la réunion client de mercredi à jeudi 14h"
"Supprime le standup de lundi"
```

**Important :** l'écriture ne fonctionne qu'avec un **fichier `.ics` local**. Les URLs Google Calendar ou Nextcloud sont en lecture seule (limitation CalDAV non implémentée).

---

## Écriture — Pipeline

```
Message utilisateur
  └─ router détecte intent calendar (keywords + ML)
  └─ CalendarWriteCrew.run() :
       1. Charge les événements existants avec indices [#0], [#1]...
       2. Calcule la table jour → date ISO (14 jours)
       3. kickoff() → LLM produit JSON :
          {
            "action": "create | update | delete",
            "event": { "title", "date", "time", "duration_minutes", ... },
            "target_uid": "#2",        ← index pour update/delete
            "confirmation_message": "..."
          }
       4. Résout l'index #N → UID complet dans le fichier ICS
       5. Appelle create_event() / update_event() / delete_event()
       6. Retourne le message de confirmation
```

---

## Écriture — Résolution des dates

Un problème courant avec les LLMs : interpréter "samedi" comme une date arbitraire. Mnemo génère une table explicite **jour → date ISO** injectée en tête du prompt :

```
lundi = 2026-03-09 <- aujourd'hui
mardi = 2026-03-10
mercredi = 2026-03-11
jeudi = 2026-03-12
vendredi = 2026-03-13
samedi = 2026-03-14
dimanche = 2026-03-15
lundi prochain = 2026-03-16
...
```

Le LLM doit utiliser **uniquement** cette table pour résoudre les jours nommés. Il ne peut pas inventer de date.

---

## Écriture — Résolution des événements (#N)

Les UIDs d'événements Google Calendar font souvent 60+ caractères. Pour éviter les erreurs de copie par le LLM, les événements sont indexés numériquement :

```
[#0] lundi 9 mars 09:00 — Standup équipe (30 min)
[#1] mercredi 11 mars 14:00 — Réunion client Acme (2h)
[#2] vendredi 13 mars — Offsite équipe (Journée)
```

Le LLM retourne `"target_uid": "#1"` et Mnemo résout `#1 → UID complet` avant d'écrire dans le fichier ICS.

---

## Écriture — Règles de l'agent

**Création (`action=create`) :**
- Utilise la table jour → ISO, jamais `today_iso` pour un autre jour
- Si plage horaire donnée (ex: "de 9h à 16h") : durée = 7h = 420 min
- Heure au format `HH:MM` (24h). `null` si journée entière

**Modification (`action=update`) :**
- `target_uid` obligatoire (index `#N`)
- Seuls les champs modifiés sont renseignés dans `event`

**Suppression (`action=delete`) :**
- `target_uid` obligatoire
- `event` peut être `null`

**Ambiguïté :** si plusieurs événements correspondent, le plus proche dans le temps est choisi et le doute est signalé dans `confirmation_message`.

---

## Limitations

| Limitation | Raison |
|------------|--------|
| Écriture impossible sur URLs distantes | CalDAV non implémenté |
| Pas de gestion multi-calendriers | ICS unique par `CALENDAR_SOURCE` |
| Récurrences en écriture : crée une copie statique | L'expansion RRULE n'est pas propagée |

---

## Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `CALENDAR_SOURCE` | (vide) | URL ou chemin ICS. Vide = désactivé |
| `CALENDAR_LOOKAHEAD_DAYS` | `14` | Fenêtre de visibilité en jours |
| `CALENDAR_CACHE_TTL` | `300` | Durée du cache en secondes |
