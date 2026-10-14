import os
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render

def manifest_view(request):
    """Sert le fichier Web App Manifest officiel avec l'en-tête MIME approprié."""
    manifest_path = os.path.join(settings.BASE_DIR, 'static', 'manifest.json')
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = "{}"
    response = HttpResponse(content, content_type='application/manifest+json')
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def service_worker_view(request):
    """Sert le Service Worker à la racine avec la portée (scope) globale autorisée."""
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = ""
    response = HttpResponse(content, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

def offline_view(request):
    """Affiche la vue dédiée lorsque le voyageur est hors connexion mobile."""
    return render(request, 'travel/offline.html')
