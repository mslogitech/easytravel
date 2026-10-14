"""
Central Asynchronous Task Dispatcher for EasyTravel.
Uses a bounded, thread-safe ThreadPoolExecutor to prevent memory exhaustion,
worker thread leaks, and slow HTTP responses caused by external APIs (Email, WhatsApp, Gemini).
"""
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, Future

logger = logging.getLogger(__name__)

# Pool de threads supervisé (limité à 6 workers pour ne pas surcharger les ressources CPU/RAM du VPS)
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="EasyTravelWorker")


def dispatch_async_task(func, *args, **kwargs):
    """
    Exécute une fonction de façon asynchrone dans le pool supervisé.
    Capture et logue automatiquement toute exception sans faire planter le serveur.
    En environnement de test (manage.py test), exécute de manière synchrone pour garantir
    l'isolation transactionnelle et éviter les verrous de table SQLite.
    """
    def _safe_wrapper(*w_args, **w_kwargs):
        try:
            return func(*w_args, **w_kwargs)
        except Exception as exc:
            logger.exception("Erreur dans la tâche asynchrone %s: %s", getattr(func, '__name__', str(func)), exc)
            return None

    if 'test' in sys.argv:
        res = _safe_wrapper(*args, **kwargs)
        f = Future()
        f.set_result(res)
        return f

    return _executor.submit(_safe_wrapper, *args, **kwargs)
