"""
Package routing — Chaîne de Responsabilité pour le routing de messages.

Usage :
    from Mnemo.routing import build_router, dispatch, RouterContext, RouterResult

    router = build_router()
    result = router.handle(RouterContext(message=msg, session_id=sid, temporal_context=ctx))
    response = dispatch(result, user_message=msg, session_id=sid, ...)
"""

from .context  import RouterContext, RouterResult
from .base     import RouterHandler
from .dispatch import build_router, dispatch

__all__ = [
    "RouterContext",
    "RouterResult",
    "RouterHandler",
    "build_router",
    "dispatch",
]