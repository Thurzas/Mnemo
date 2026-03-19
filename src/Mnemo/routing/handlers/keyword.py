"""
KeywordHandler — détection déterministe par correspondance de mots-clés.

Confiance absolue (1.0) si match — court-circuite le ML et le LLM.
Note : les keywords forts sont des signaux non-ambigus.
       Les keywords faibles (_SCHEDULER_KEYWORDS_WEAK) ne déclenchent PAS
       de bypass seuls — ils servent de hints pour le MLHandler via ctx._hints.
"""

from ..base import RouterHandler
from ..context import RouterContext, RouterResult


# ── Shell ────────────────────────────────────────────────────────────────────

_SHELL_KEYWORDS = [
    "liste les fichiers", "liste les dossiers", "liste le dossier",
    "lister les fichiers", "lister les dossiers",
    "qu est-ce qu il y a dans", "contenu du dossier",
    "montre-moi les fichiers", "montre moi les fichiers",
    "va dans le dossier", "va fouiller dans",
    "lis le fichier", "lit le fichier", "lire le fichier",
    "affiche le fichier", "afficher le fichier",
    "montre-moi le fichier", "montre moi le fichier",
    "cherche les fichiers", "trouve les fichiers",
    "find /data", "ls /data", "cat /data",
    "cree le dossier", "cree un dossier",
    "cree le fichier", "cree un fichier",
    "supprime le fichier", "supprime le dossier",
    "deplace le fichier", "copie le fichier",
    "lance le script", "execute le script",
    "commande shell", "commande systeme", "en shell", "via shell",
    "liste moi les", "liste-moi les",
]


def _detect_shell_intent(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _SHELL_KEYWORDS)


# ── Scheduler ────────────────────────────────────────────────────────────────

# Keywords forts → bypass LLM déterministe (non ambigus)
# NOTE : "planifie" et "planifier" seuls ont été retirés — trop ambigus.
#   "planifier un projet/des étapes" → route plan, pas scheduler.
#   Seules les formulations avec contexte rappel/tâche sont gardées.
_SCHEDULER_KEYWORDS_STRONG = [
    "rappelle-moi", "rappelle moi",
    "planifie un rappel", "planifie une tâche", "planifie une alerte",
    "planifier un rappel", "planifier une tâche", "planifier une alerte",
    "chaque lundi", "chaque mardi", "chaque mercredi", "chaque jeudi",
    "chaque vendredi", "chaque samedi", "chaque dimanche",
    "tous les lundis", "tous les mardis", "tous les mercredis",
    "tous les jeudis", "tous les vendredis",
    "chaque semaine", "chaque jour", "tous les jours",
    "tous les matins", "chaque matin",
    "annule le rappel", "annule la tâche", "supprime le rappel",
    "liste mes rappels", "liste mes tâches planifiées",
    "quels sont mes rappels",
    "programme le rappel",
    "programmer un rappel", "programmer une tâche",
]

# Keywords faibles → hint pour le ML uniquement (restent ambigus sans ML)
_SCHEDULER_KEYWORDS_WEAK = [
    "programme un ", "programme une ", "programme ce ", "programme cette ",
    "programme mon ", "programme ma ",
    "programmer un ", "programmer une ", "programmer ce ", "programmer cette ",
    "dans 1 jour", "dans 2 jours", "dans 3 jours",
    "demain matin", "demain soir",
]


def _detect_scheduler_intent(msg: str) -> tuple[bool, bool]:
    """Retourne (strong, weak) — strong = bypass LLM, weak = hint ML."""
    m      = msg.lower()
    strong = any(kw in m for kw in _SCHEDULER_KEYWORDS_STRONG)
    weak   = any(kw in m for kw in _SCHEDULER_KEYWORDS_WEAK)
    return strong, weak


# ── Calendar write ────────────────────────────────────────────────────────────

_CALENDAR_WRITE_KEYWORDS = [
    # Création — impératif direct
    "ajoute un événement", "ajoute un evenement", "ajoute un rdv", "ajoute un rendez-vous",
    "crée un événement", "cree un evenement", "crée un rdv", "crée un rendez-vous",
    "nouveau rendez-vous", "nouvel événement", "nouvel evenement",
    "mets un événement", "mets un evenement", "met un événement", "met un evenement",
    "planifie un événement", "planifie un evenement", "planifie un rendez-vous",
    # Création — vers agenda/calendrier
    "ajoute à mon agenda", "ajoute dans mon agenda", "ajoute à l'agenda",
    "inscris dans mon agenda", "inscris à mon agenda",
    "bloque dans mon calendrier", "bloque le créneau", "bloque ce créneau",
    # Suppression — impératif direct
    "supprime l'événement", "supprime l'evenement", "supprime le rendez-vous", "supprime le rdv",
    "annule l'événement", "annule l'evenement", "annule le rendez-vous", "annule le rdv",
    "efface l'événement", "efface l'evenement", "efface le rendez-vous",
    "enlève l'événement", "enleve l'evenement", "enlève le rendez-vous",
    # Suppression — infinitif + lieu ("tu peux me le supprimer du calendrier")
    "supprimer du calendrier", "supprimer de mon calendrier", "supprimer de l'agenda",
    "supprimer de mon agenda", "enlever du calendrier", "enlever de l'agenda",
    "retirer du calendrier", "retirer de l'agenda", "effacer du calendrier",
    # Modification — impératif / infinitif
    "décale mon rendez-vous", "decale mon rendez-vous", "décale l'événement", "decale l'evenement",
    "modifie mon rendez-vous", "modifie l'événement", "modifie l'evenement",
    "déplace mon rendez-vous", "deplace mon rendez-vous", "déplace l'événement",
    "repousse le rendez-vous", "repousse l'événement", "avance le rendez-vous",
    "change l'heure de mon rendez-vous", "change la date de mon rendez-vous",
]

# Détection par co-occurrence : verbe d'action + mot contexte calendrier
_CAL_ACTION_VERBS = [
    "supprimer", "effacer", "enlever", "retirer", "annuler",
    "ajouter", "créer", "creer", "insérer", "inserer", "planifier",
    "modifier", "changer", "déplacer", "deplacer", "décaler", "decaler",
    "repousser", "avancer",
    "supprime", "efface", "enlève", "enleve", "retire", "annule",
    "ajoute", "crée", "cree", "insère", "insere", "planifie",
    "modifie", "déplace", "deplace", "décale", "decale",
    "repousse", "avance", "mets",
]
_CAL_CONTEXT_WORDS = [
    "calendrier", "agenda",
    "événement", "evenement", "évènement", "evènement",
    "event", "l'event", "cet event",
    "rdv", "rendez-vous", "rendez vous",
    "créneau", "creneau",
]


def _detect_calendar_write_intent(msg: str) -> bool:
    m = msg.lower()
    if any(kw in m for kw in _CALENDAR_WRITE_KEYWORDS):
        return True
    has_verb = any(v in m for v in _CAL_ACTION_VERBS)
    has_ctx  = any(c in m for c in _CAL_CONTEXT_WORDS)
    return has_verb and has_ctx


# ── Plan ──────────────────────────────────────────────────────────────────────

_PLAN_KEYWORDS_STRONG = [
    "construis-moi", "construis moi",
    "développe", "developpe",
    "implémente", "implemente",
    "prépare un plan", "prepare un plan",
    "fais-moi un plan", "fais moi un plan",
    "décompose la tâche", "decompose la tache",
    "organise les étapes", "organise les etapes",
    "crée un plan", "cree un plan",
    "écris un plan", "ecris un plan",
    "planifie le développement", "planifie le developpement",
    "rédige le plan", "redige le plan",
]

# Keywords faibles — hint pour ML (ambigus sans contexte)
_PLAN_KEYWORDS_WEAK = [
    "comment implémenter", "comment implementer",
    "comment développer", "comment developper",
    "comment construire",
    "par où commencer", "par ou commencer",
    # Formulations projet/organisation (≠ rappel scheduler)
    "planifier ce projet", "planifier le projet",
    "planifier en étapes", "planifier les étapes",
    "planifier en etapes", "planifier les etapes",
    "organiser ce projet", "organiser le projet",
    "préparer un projet", "preparer un projet",
    "en étapes pour", "en etapes pour",
    "découper en étapes", "decouper en etapes",
]


def _detect_plan_intent(msg: str) -> tuple[bool, bool]:
    """Retourne (strong, weak).

    Les keywords multi-mots sont testés par substring (suffisant car non ambigus).
    Les keywords mono-mots (verbes impératifs courts) sont testés avec \b pour
    éviter de matcher des formes infinitives (ex: "implémente" ≠ "implémenter").
    """
    import re
    m    = msg.lower()
    strong = False
    for kw in _PLAN_KEYWORDS_STRONG:
        if " " in kw or "-" in kw:
            if kw in m:
                strong = True
                break
        else:
            if re.search(r"\b" + re.escape(kw) + r"\b", m):
                strong = True
                break
    weak = any(kw in m for kw in _PLAN_KEYWORDS_WEAK)
    return strong, weak


# ── Note ──────────────────────────────────────────────────────────────────────

_NOTE_KEYWORDS = [
    "note que", "notes que", "retiens que", "retiens bien que",
    "mémorise que", "mémorise ça", "mémorise ceci",
    "n'oublie pas que", "n'oublie pas ça",
    "souviens-toi que", "souviens toi que",
    "garde en mémoire", "garde ça en mémoire",
    "enregistre que", "enregistre ceci", "enregistre ça",
    "ajoute à ma mémoire", "ecris dans ma memoire", "écris dans ma mémoire",
    "ajoute à mes notes", "ajoute dans mes notes",
    "important à noter", "important a noter",
    "à noter :", "a noter :",
]


def _detect_note_intent(msg: str) -> bool:
    m = msg.lower()
    return any(kw in m for kw in _NOTE_KEYWORDS)


# ── Handler ───────────────────────────────────────────────────────────────────

# Seuil de mots au-delà duquel le bypass keyword est désactivé pour les routes
# ambiguës (scheduler, plan, calendar). Les messages longs sont des discussions,
# pas des commandes directes — le ML/LLM est plus fiable dans ce cas.
# Shell et Note restent actifs quelle que soit la longueur (impératifs de sécurité/mémoire).
_KEYWORD_BYPASS_MAX_WORDS = 12


class KeywordHandler(RouterHandler):
    """
    Détection déterministe — confiance absolue (1.0) si match.

    Dépose aussi des hints dans ctx._hints pour les handlers suivants :
      - kw_shell  : hint pour MLHandler (arbitrage arbitrage)
      - kw_sched_weak : hint pour MLHandler (scheduler faible signal)
    """

    def handle(self, ctx: RouterContext) -> RouterResult | None:
        msg  = ctx.message
        _short = len(msg.split()) <= _KEYWORD_BYPASS_MAX_WORDS

        # ── Note — priorité max, pas de limite de longueur ────────────────
        if _detect_note_intent(msg):
            return RouterResult("note", 1.0, "keyword")

        if _short:
            # ── Plan fort ─────────────────────────────────────────────────
            plan_strong, plan_weak = _detect_plan_intent(msg)
            if plan_strong:
                return RouterResult("plan", 1.0, "keyword", {"needs_recon": True})

            # ── Calendar write ────────────────────────────────────────────
            if _detect_calendar_write_intent(msg):
                return RouterResult("calendar", 1.0, "keyword")

            # ── Scheduler fort ────────────────────────────────────────────
            strong, weak = _detect_scheduler_intent(msg)
            if strong:
                return RouterResult("scheduler", 1.0, "keyword")
        else:
            # Message long → pas de bypass, mais on calcule quand même les hints
            _, plan_weak   = _detect_plan_intent(msg)
            _, weak        = _detect_scheduler_intent(msg)

        # ── Dépôt des hints pour les handlers aval ────────────────────────
        ctx._hints["kw_shell"]      = _detect_shell_intent(msg)
        ctx._hints["kw_sched_weak"] = weak
        ctx._hints["kw_plan_weak"]  = plan_weak

        return self._pass(ctx)