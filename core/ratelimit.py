"""
Lightweight in-memory Rate Limiting decorator for EasyTravel.
Protects payment endpoints, reservation creation, and authentication from abuse, brute force, and flood.
"""
from functools import wraps
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.contrib import messages


def get_client_ip(request):
    """Extrait l'adresse IP réelle du client même derrière Nginx / reverse proxy."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
    return ip


def ratelimit(max_requests=20, window_seconds=60, block_message="Trop de requêtes. Veuillez patienter un instant avant de réessayer."):
    """
    Décorateur de limitation de débit (rate limiting) par IP / session.
    Protège les formulaires d'authentification, de réservation et de paiement contre les abus et le spam.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            ip = get_client_ip(request)
            cache_key = f"ratelimit:{view_func.__name__}:{ip}"
            
            try:
                current_count = cache.get(cache_key, 0)
            except Exception:
                current_count = 0

            if current_count >= max_requests:
                is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
                if is_ajax:
                    return JsonResponse({
                        'status': 'error',
                        'message': block_message
                    }, status=429)

                if hasattr(request, '_messages'):
                    try:
                        messages.error(request, block_message)
                    except Exception:
                        pass

                return HttpResponse(
                    f"<div style='font-family:sans-serif;text-align:center;padding:50px;'>"
                    f"<h2>Limite de requêtes atteinte</h2>"
                    f"<p>{block_message}</p>"
                    f"<p><small>Code HTTP 429 - Réessayez dans quelques secondes.</small></p>"
                    f"</div>", 
                    status=429
                )

            try:
                cache.set(cache_key, current_count + 1, window_seconds)
            except Exception:
                pass

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator
