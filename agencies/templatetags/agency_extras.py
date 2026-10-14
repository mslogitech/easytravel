import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter(name='format_money')
def format_money(value):
    """
    Formate un montant avec un espace comme séparateur de milliers.
    Ex: 155000 -> 155 000
    """
    if value is None or value == "":
        return "0"
    try:
        val = int(round(float(value)))
        return f"{val:,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(value)

@register.filter(name='format_phone')
def format_phone(phone_str):
    """
    Détecte et formate uniformément les numéros de téléphone avec badge pays et espacement lisible.
    - Numéros camerounais (6XXXXXXXX ou +237 / 237) -> Badge +237 et format '6XX XXX XXX' (ex: 656 055 407)
    - Numéros allemands / européens (0176... ou +49...) -> Badge +49 et format '176 3532 1633'
    - Numéros français (06/07... ou +33...) -> Badge +33 et format '6 XX XX XX XX'
    - Autres numéros internationaux -> Badge indicatif et découpage espacé
    """
    if not phone_str:
        return mark_safe('<span class="text-muted fw-bold">-</span>')
    
    raw = str(phone_str).strip()
    
    # Nettoyage des préfixes en double (ex: "+237 +237", "+237 237", "00237 237")
    raw = re.sub(r'(\+?237[\s\-.]*)+', '+237 ', raw).strip()
    raw = re.sub(r'(\+?49[\s\-.]*)+', '+49 ', raw).strip()
    raw = re.sub(r'(\+?33[\s\-.]*)+', '+33 ', raw).strip()

    digits = re.sub(r'[^\d+]', '', raw)
    
    # 1. Cas Cameroun (+237, 237, 00237, ou 9 chiffres locaux débutant par 6 ou 2)
    core_cmr = None
    if digits.startswith('+237'):
        core_cmr = digits[4:]
    elif digits.startswith('00237'):
        core_cmr = digits[5:]
    elif digits.startswith('237') and len(digits) >= 12:
        core_cmr = digits[3:]
    elif len(digits) == 9 and digits[0] in ('6', '2'):
        core_cmr = digits

    # Sécurité supplémentaire si des 237 résiduels existent
    if core_cmr and core_cmr.startswith('237') and len(core_cmr) == 12:
        core_cmr = core_cmr[3:]

    if core_cmr and len(core_cmr) == 9:
        # Format 3-3-3 : "656 055 407" tel que demandé explicitement
        formatted_num = f"{core_cmr[:3]} {core_cmr[3:6]} {core_cmr[6:]}"
        return mark_safe(
            f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
            f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">+237</span>'
            f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{formatted_num}</span>'
            f'</div>'
        )
    
    # 2. Cas Allemagne / Europe (mobile allemand 015x, 016x, 017x ou +49)
    if digits.startswith('+49'):
        core = digits[3:]
        if core.startswith('0'):
            core = core[1:]
        # ex: +49 176 3532 1633
        if len(core) >= 10:
            formatted = f"{core[:3]} {core[3:7]} {core[7:]}"
        else:
            chunks = [core[i:i+3] for i in range(0, len(core), 3)]
            formatted = " ".join(chunks)
        return mark_safe(
            f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
            f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">+49</span>'
            f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{formatted}</span>'
            f'</div>'
        )
    elif digits.startswith('01') and len(digits) in (11, 12):
        # Format national allemand (ex: 017635321633 -> indicatif +49, affichage 176 3532 1633)
        core = digits[1:]
        formatted = f"{core[:3]} {core[3:7]} {core[7:]}"
        return mark_safe(
            f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
            f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">+49</span>'
            f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{formatted}</span>'
            f'</div>'
        )

    # 3. Cas France (+33 ou 06/07)
    if digits.startswith('+33'):
        core = digits[3:]
        if core.startswith('0'):
            core = core[1:]
        chunks = [core[0]] + [core[i:i+2] for i in range(1, len(core), 2)]
        return mark_safe(
            f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
            f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">+33</span>'
            f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{" ".join(chunks)}</span>'
            f'</div>'
        )
    elif len(digits) == 10 and digits.startswith(('06', '07')):
        core = digits[1:]
        chunks = [core[0]] + [core[i:i+2] for i in range(1, len(core), 2)]
        return mark_safe(
            f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
            f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">+33</span>'
            f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{" ".join(chunks)}</span>'
            f'</div>'
        )
    
    # 4. Cas International générique avec indicatif '+'
    if digits.startswith('+'):
        match = re.match(r'(\+\d{1,3})(.*)', digits)
        if match:
            code, rest = match.groups()
            chunks = [rest[i:i+3] for i in range(0, len(rest), 3)]
            formatted_num = " ".join(chunks)
            return mark_safe(
                f'<div class="d-inline-flex align-items-center gap-1.5 text-nowrap">'
                f'<span class="badge border bg-light text-dark px-1.5 py-0.5 fw-semibold" style="font-size: 0.72rem;">{code}</span>'
                f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{formatted_num}</span>'
                f'</div>'
            )
            
    # 5. Découpage par blocs de 3 si chiffres purs non identifiés
    chunks = [digits[i:i+3] for i in range(0, len(digits), 3)]
    formatted_num = " ".join(chunks)
    return mark_safe(f'<span class="font-monospace text-dark fw-medium" style="font-size: 0.84rem;">{formatted_num}</span>')


@register.filter(name='format_route')
def format_route(route_name):
    """
    Remplace les caractères typographiques '->', '<->', ou ' - ' par une icône vectorielle SVG
    insécable sur une seule ligne (d-inline-flex align-items-center text-nowrap)
    pour éliminer tout saut de ligne inesthétique de la flèche.
    """
    if not route_name:
        return ""
    
    s = str(route_name).strip()
    arrow_svg = (
        '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-primary flex-shrink-0 mx-1">'
        '<line x1="5" y1="12" x2="19" y2="12"></line>'
        '<polyline points="12 5 19 12 12 19"></polyline>'
        '</svg>'
    )
    
    # Vérifier les séparateurs courants : '->', '-->', ' - '
    if '->' in s:
        parts = s.split('->', 1)
        return mark_safe(f'<span class="d-inline-flex align-items-center text-nowrap"><span class="fw-semibold text-dark">{parts[0].strip()}</span>{arrow_svg}<span class="fw-semibold text-dark">{parts[1].strip()}</span></span>')
    elif ' - ' in s:
        parts = s.split(' - ', 1)
        return mark_safe(f'<span class="d-inline-flex align-items-center text-nowrap"><span class="fw-semibold text-dark">{parts[0].strip()}</span>{arrow_svg}<span class="fw-semibold text-dark">{parts[1].strip()}</span></span>')
    
    return mark_safe(f'<span class="fw-semibold text-dark text-nowrap">{s}</span>')


@register.filter(name='highlight_audit_data')
def highlight_audit_data(text):
    """
    Met en évidence visuelle immédiate les variables critiques dans les explications d'audit :
    - Montants et écarts financiers (ex: 150,000 FCFA, +5,000 FCFA, -3 000 FCFA)
    - Numéros de CNI et passeports (ex: CNI N° 10125, pièce CNI « 1122334455 »)
    - Références de billets (ex: ET-XXXXXXXX)
    - Pourcentages de dérive (ex: 38.5%, 20%)
    - Statuts entre guillemets (« Embarqué », « ticket volant »)
    """
    if not text:
        return ""
    
    s = str(text)
    
    # 1. Montants financiers et écarts chiffrés (ex: +5,000 FCFA, -3 000 FCFA, 150,000 FCFA, 5 000 FCFA)
    s = re.sub(
        r'([+-]?\b\d[\d\s,.]*\s*(?:FCFA|F\s*CFA))',
        r'<strong class="text-dark bg-warning bg-opacity-25 px-1.5 py-0.5 rounded font-monospace fw-bold border border-warning border-opacity-50">\1</strong>',
        s
    )
    
    # 2. Numéros de pièces d'identité (ex: CNI N° 10125, Pièce CNI N° 10125, CNI « 1122334455 », etc.)
    s = re.sub(
        r'((?:(?:Pi[eèé]ce|Document)\s+)?(?:CNI|Passeport|R[eé]c[eé]piss[eé])\s*(?:N[°o.]?|n[°o.]?|#)?\s*(?:«\s*[^»]+\s*»|[A-Za-z0-9_-]+))',
        r'<strong class="text-dark bg-info bg-opacity-15 px-1.5 py-0.5 rounded font-monospace fw-bold border border-info border-opacity-50">\1</strong>',
        s,
        flags=re.IGNORECASE
    )
    
    # 3. Références de billets (ex: ET-XXXXXXXX)
    s = re.sub(
        r'\b(ET-[A-Z0-9]{8})\b',
        r'<strong class="text-primary bg-primary bg-opacity-10 px-1.5 py-0.5 rounded font-monospace fw-bold border border-primary border-opacity-25">\1</strong>',
        s
    )
    
    # 4. Pourcentages (ex: 38.5%, 20%)
    s = re.sub(
        r'(\b\d+(?:\.\d+)?%)',
        r'<strong class="text-danger fw-bold">\1</strong>',
        s
    )
    
    # 5. Expressions clés entre guillemets (ex: « Embarqué », « ticket volant »)
    s = re.sub(
        r'(«\s*(?:Embarqué|En attente|Non réglé|ticket volant)\s*»)',
        r'<strong class="text-dark fw-bold">\1</strong>',
        s
    )

    return mark_safe(s)


