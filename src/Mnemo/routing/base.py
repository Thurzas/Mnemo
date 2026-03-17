"""
RouterHandler — classe de base abstraite pour la chaîne de responsabilité.

Chaque handler répond à une seule question :
  « Puis-je déterminer l'intention avec suffisamment de confiance ? »

Si oui  → retourne un RouterResult et court-circuite la chaîne.
Si non  → appelle self._pass(ctx) pour déléguer au handler suivant.
"""

from abc import ABC, abstractmethod

from .context import RouterContext, RouterResult


class RouterHandler(ABC):
    def __init__(self) -> None:
        self._next: "RouterHandler | None" = None

    def set_next(self, handler: "RouterHandler") -> "RouterHandler":
        """Chaîne le handler suivant et le retourne (pour le fluent chaining)."""
        self._next = handler
        return handler

    @abstractmethod
    def handle(self, ctx: RouterContext) -> RouterResult | None:
        """Tente de router. Retourne un RouterResult ou None si pas de décision."""
        ...

    def _pass(self, ctx: RouterContext) -> RouterResult | None:
        """Délègue au handler suivant, ou retourne None si fin de chaîne."""
        return self._next.handle(ctx) if self._next else None