import re
from django.core.exceptions import ValidationError
from django.core.validators import validate_email as django_validate_email

# Liste des mots et expressions interdits pour les noms de passagers ou pièces d'identité
FORBIDDEN_NAME_PATTERNS = {
    'test', 'fraud', 'faux', 'clandestin', 'passager', 'client',
    'inconnu', 'voyageur', 'fake', 'dummy', 'asdf', 'qwerty',
    'admin', 'user', 'null', 'undefined', 'anonyme'
}

# Préfixes mobiles valides au Cameroun (9 chiffres)
# Orange Cameroun : 690-699, 655-659
# MTN Cameroun : 670-679, 680-689, 650-654
# Nexttel : 660-669
# Camtel (Mobile / CTPhone) : 620-629
# Fixes nationaux : 222, 233, 242, 243
CAMEROON_MOBILE_PREFIXES = (
    # Orange
    '690', '691', '692', '693', '694', '695', '696', '697', '698', '699',
    '655', '656', '657', '658', '659',
    # MTN
    '670', '671', '672', '673', '674', '675', '676', '677', '678', '679',
    '680', '681', '682', '683', '684', '685', '686', '687', '688', '689',
    '650', '651', '652', '653', '654',
    # Nexttel
    '660', '661', '662', '663', '664', '665', '666', '667', '668', '669',
    # Camtel
    '620', '621', '622', '623', '624', '625', '626', '627', '628', '629',
)

CAMEROON_LANDLINE_PREFIXES = ('222', '233', '242', '243')


def validate_passenger_name(name_raw):
    """
    Valide le nom complet d'un passager selon les exigences légales du Ministère des Transports :
    - Au moins Nom et Prénom (minimum 2 mots distincts)
    - Chaque mot doit comporter au moins 2 lettres alphabétiques
    - Caractères autorisés : lettres (y compris accents français), tirets, apostrophes, espaces
    - Rejet strict des faux noms, noms de test ou termes factices (ex: 'Passager Clandestin', 'test test')
    """
    if not name_raw:
        raise ValidationError("Le nom complet du passager est obligatoire.")

    cleaned = str(name_raw).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    if len(cleaned) < 4:
        raise ValidationError("Le nom complet est trop court (au moins 4 caractères requis).")
    if len(cleaned) > 80:
        raise ValidationError("Le nom complet ne doit pas dépasser 80 caractères.")

    # Vérification des caractères autorisés (lettres, espaces, tirets, apostrophes)
    if not re.match(r"^[A-Za-zÀ-ÿ\s\'-]+$", cleaned):
        raise ValidationError("Le nom ne doit contenir que des lettres alphabétiques, tirets et apostrophes (chiffres et symboles interdits).")

    words = cleaned.split(' ')

    for w in words:
        # Nettoyer les tirets/apostrophes pour compter les lettres
        letters_only = re.sub(r"[^A-Za-zÀ-ÿ]", "", w)
        if len(letters_only) < 2:
            raise ValidationError(f"Le nom doit comporter au moins 2 lettres (partie invalide : '{w}').")

    # Vérification anti-fraude / anti-test
    lower_val = cleaned.lower()
    for forbidden in FORBIDDEN_NAME_PATTERNS:
        if re.search(rf"\b{re.escape(forbidden)}\b", lower_val):
            raise ValidationError(f"Nom invalide ou non autorisé ('{forbidden}'). Veuillez fournir un nom et prénom d'état civil réels.")

    # Rejeter les répétitions pures de caractères (ex: 'aaaa bbbb', 'zzzzz yyyyy')
    pure_letters = re.sub(r'[^A-Za-zÀ-ÿ]', '', lower_val)
    if len(set(pure_letters)) < 3:
        raise ValidationError("Le nom saisi est invalide (caractères répétitifs ou incohérents).")

    # Mettre en forme proprement (majuscule au début de chaque mot, en préservant les tirets)
    parts = []
    for w in words:
        subparts = [sub.capitalize() for sub in w.split('-')]
        parts.append('-'.join(subparts))
    return ' '.join(parts)


def validate_cameroon_phone(phone_raw):
    """
    Valide et normalise un numéro de téléphone avec détection intelligente de l'indicatif régional :
    - Cameroun (+237) : 9 chiffres débutant par un préfixe opérateur réel (Orange, MTN, Nexttel, Camtel, Fixe)
      -> Rejet impératif des numéros fictifs, répétitions de chiffres ou faux préfixes.
    - France (+33) : indicatif +33 ou format 06/07 -> +33 6 XX XX XX XX
    - Allemagne (+49) : indicatif +49 ou format 01xx -> +49 1XX XXXX XXXX
    - International E.164 (+...) : de 8 à 15 chiffres, cohérence anti-fraude (au moins 4 chiffres distincts)
    """
    if not phone_raw:
        raise ValidationError("Le numéro de téléphone est obligatoire.")

    raw = str(phone_raw).strip()
    # Nettoyage des préfixes en double (ex: "+237 +237", "+49 +49", "+33 +33")
    raw = re.sub(r'(\+?237[\s\-.]*){2,}', '+237 ', raw)
    raw = re.sub(r'(\+?49[\s\-.]*){2,}', '+49 ', raw)
    raw = re.sub(r'(\+?33[\s\-.]*){2,}', '+33 ', raw)

    digits = re.sub(r'[^\d+]', '', raw)
    if digits.startswith('+4949'):
        digits = '+49' + digits[5:]
    elif digits.startswith('+237237'):
        digits = '+237' + digits[7:]
    elif digits.startswith('+3333'):
        digits = '+33' + digits[5:]

    clean_pure_digits = re.sub(r'\D', '', digits)

    if not clean_pure_digits:
        raise ValidationError("Numéro de téléphone invalide : aucun chiffre détecté.")

    # Vérification anti-numéro bidon global (répétitions pures de chiffres ex: 000000000, 666666666, 600000000)
    if len(clean_pure_digits) >= 8:
        if len(set(clean_pure_digits)) <= 2:
            raise ValidationError("Numéro de téléphone invalide : les numéros composés de chiffres répétitifs ne sont pas acceptés.")
        from collections import Counter
        counts = Counter(clean_pure_digits)
        if any(count >= 7 for count in counts.values()) and len(clean_pure_digits) <= 10:
            raise ValidationError("Numéro de téléphone invalide : les numéros composés de chiffres répétitifs ne sont pas acceptés.")

    # 1. Cas Allemagne (+49, 0049 ou préfixe national 01xx)
    if digits.startswith('+49') or digits.startswith('0049'):
        core = clean_pure_digits[2:] if digits.startswith('+49') else clean_pure_digits[4:]
        if core.startswith('0'):
            core = core[1:]  # Suppression du 0 indicatif national en format international
        if len(core) < 9 or len(core) > 13:
            raise ValidationError("Numéro allemand invalide : doit comporter entre 9 et 12 chiffres après l'indicatif +49.")
        return f"+49 {core[:3]} {core[3:7]} {core[7:]}".strip()
    elif digits.startswith('01') and len(clean_pure_digits) in (11, 12):
        core = clean_pure_digits[1:]
        return f"+49 {core[:3]} {core[3:7]} {core[7:]}".strip()

    # 2. Cas France (+33, 0033 ou préfixe 06/07)
    if digits.startswith('+33') or digits.startswith('0033'):
        core = clean_pure_digits[2:] if digits.startswith('+33') else clean_pure_digits[4:]
        if core.startswith('0'):
            core = core[1:]  # Suppression du 0 indicatif national en format international
        if len(core) != 9:
            raise ValidationError("Numéro français invalide : doit comporter 9 chiffres après l'indicatif +33.")
        return f"+33 {core[0]} {core[1:3]} {core[3:5]} {core[5:7]} {core[7:9]}"
    elif len(clean_pure_digits) == 10 and clean_pure_digits.startswith(('06', '07')):
        core = clean_pure_digits[1:]
        return f"+33 {core[0]} {core[1:3]} {core[3:5]} {core[5:7]} {core[7:9]}"

    # 3. Cas Cameroun (+237, 237, 00237, ou 9 chiffres locaux débutant par 6 ou 2)
    core_cmr = clean_pure_digits
    if core_cmr.startswith('00237'):
        core_cmr = core_cmr[5:]
    elif core_cmr.startswith('237') and len(core_cmr) == 12:
        core_cmr = core_cmr[3:]

    if len(core_cmr) == 9:
        prefix3 = core_cmr[:3]
        prefix1 = core_cmr[0]

        if prefix1 not in ('6', '2'):
            raise ValidationError("Numéro camerounais invalide : les numéros au Cameroun commencent par un 6 (Orange, MTN, Nexttel, Camtel) ou un 2 (fixes).")

        # Vérifier que le préfixe de 3 chiffres correspond à un opérateur actif au Cameroun
        if prefix1 == '6':
            if prefix3 not in CAMEROON_MOBILE_PREFIXES:
                raise ValidationError(f"Préfixe mobile '{prefix3}' inconnu au Cameroun. Les préfixes valides sont Orange (69x, 655-659), MTN (67x, 68x, 650-654), Nexttel (66x) ou Camtel (62x).")
            return f"+237 {core_cmr[:3]} {core_cmr[3:6]} {core_cmr[6:]}"
        elif prefix1 == '2':
            if prefix3 not in CAMEROON_LANDLINE_PREFIXES:
                raise ValidationError(f"Préfixe fixe '{prefix3}' non reconnu au Cameroun. Les préfixes fixes valides sont 222, 233, 242 ou 243.")
            return f"+237 {core_cmr[:3]} {core_cmr[3:5]} {core_cmr[5:7]} {core_cmr[7:]}"

    # 4. Cas International générique avec indicatif '+' ou '00'
    if digits.startswith('+') or digits.startswith('00'):
        int_digits = clean_pure_digits if digits.startswith('+') else clean_pure_digits[2:]
        if len(int_digits) < 8 or len(int_digits) > 15:
            raise ValidationError("Numéro international invalide : doit comporter entre 8 et 15 chiffres selon la norme internationale E.164.")
        
        # Formatage lisible
        if digits.startswith('+'):
            match = re.match(r'(\+\d{1,3})(.*)', digits)
            if match:
                code, rest = match.groups()
                chunks = [rest[i:i+3] for i in range(0, len(rest), 3)]
                return f"{code} {' '.join(chunks)}"
        return f"+{int_digits[:3]} {' '.join([int_digits[i:i+3] for i in range(3, len(int_digits), 3)])}"

    # Si moins de 9 chiffres sans indicatif
    if len(clean_pure_digits) < 9:
        raise ValidationError("Numéro de téléphone incomplet (au moins 9 chiffres requis pour un numéro national ou 8 à 15 chiffres avec indicatif).")

    raise ValidationError("Format de numéro non reconnu. Veuillez inclure l'indicatif (ex: +237 pour le Cameroun, +33 pour la France, +49 pour l'Allemagne).")


# Alias pour clarté
validate_phone_number = validate_cameroon_phone


def validate_clean_email(email_raw):
    """
    Valide et normalise une adresse email :
    - Présence obligatoire
    - Format RFC valide via Django
    - Vérification du domaine et TLD
    - Rejet des domaines/adresses factices
    """
    if not email_raw or not str(email_raw).strip():
        raise ValidationError("L'adresse email est obligatoire pour recevoir vos billets et notifications.")
    
    email = str(email_raw).strip().lower()
    django_validate_email(email)

    domain_part = email.split('@')[1]
    if '.' not in domain_part:
        raise ValidationError("L'adresse email doit comporter un nom de domaine valide avec extension (ex: .com, .cm, .fr).")
    
    tld = domain_part.split('.')[-1]
    if len(tld) < 2 or not tld.isalpha():
        raise ValidationError(f"Extension de domaine '{tld}' invalide dans l'adresse email.")

    # Rejeter les adresses de test factices évidentes (sauf en test unitaire si nécessaire)
    if email in ('test@test.com', 'fake@fake.com', 'user@example.com', 'email@test.cm'):
        raise ValidationError("Veuillez fournir une véritable adresse email personnelle ou professionnelle valide.")

    return email


def validate_identity_info(id_type, id_number):
    """
    Valide rigoureusement le type et le numéro de la pièce d'identité selon les normes des agences au Cameroun :
    - CNI : Carte Nationale d'Identité camerounaise (9 à 11 caractères, au moins 70% de chiffres, anti-fraude)
    - PASSPORT : Passeport officiel (7 à 12 caractères alphanumériques, mélange lettres et chiffres, anti-fraude)
    - RECEIPT : Récépissé d'identité officiel délivré par la DGSN (8 à 16 caractères alphanumériques, anti-fraude)
    - STUDENT : Carte d'étudiant
    - OTHER : Autre ou aucune pièce (aucune validation stricte du numéro requise)
    """
    valid_types = ['CNI', 'PASSPORT', 'RECEIPT', 'STUDENT', 'OTHER']
    if id_type not in valid_types:
        raise ValidationError("Type de pièce d'identité invalide. Choisissez CNI, Passeport, Récépissé, Étudiant ou Autre.")

    if id_type == 'OTHER':
        # Si c'est autre/sans pièce, on accepte un numéro vide ou n'importe quoi (nettoyé)
        return id_type, str(id_number).strip().upper() if id_number else "N/A"

    if not id_number:
        raise ValidationError("Le numéro de la pièce d'identité (ou Matricule) est obligatoire.")

    raw = str(id_number).strip().upper()
    # Nettoyer les espaces et tirets
    clean = re.sub(r'[\s\-]', '', raw)

    if len(clean) < 4:
        raise ValidationError("Le numéro saisi est trop court (au moins 4 caractères requis).")
    if len(clean) > 20:
        raise ValidationError("Le numéro saisi est trop long (maximum 20 caractères).")

    # Vérification anti-fraude / anti-valeurs factices
    lower_clean = clean.lower()
    for forbidden in FORBIDDEN_NAME_PATTERNS:
        if forbidden in lower_clean:
            raise ValidationError(f"Numéro invalide ('{forbidden}' détecté). Veuillez renseigner votre véritable numéro officiel.")

    # Vérification anti-répétition pure (ex: '00000000', '11111111', 'AAAAAAAA')
    if len(set(clean)) < 3:
        raise ValidationError("Numéro invalide : suite de caractères répétitifs non autorisée.")

    # Vérification des suites consécutives triviales (ex: '12345678', '98765432')
    digits_only = re.sub(r'\D', '', clean)
    if digits_only in ('12345678', '123456789', '1234567890', '987654321', '012345678'):
        raise ValidationError("Numéro invalide : séquence numérique fictive détectée.")

    if id_type == 'CNI':
        if not re.match(r'^[A-Z0-9]+$', clean):
            raise ValidationError("Le numéro de CNI ne doit contenir que des chiffres et des lettres majuscules.")
        # La CNI camerounaise moderne comporte 9 à 11 chiffres (parfois précédés d'une lettre de région comme CE)
        if len(clean) < 8 or len(clean) > 13:
            raise ValidationError("Le numéro de CNI doit comporter entre 8 et 12 caractères (ex: 102938475 ou 1102938475).")
        # Doit contenir une majorité de chiffres
        digit_count = sum(1 for c in clean if c.isdigit())
        if digit_count < 7 or (digit_count / len(clean)) < 0.70:
            raise ValidationError("Le numéro de CNI doit être composé principalement de chiffres (ex: 102938475).")

    elif id_type == 'PASSPORT':
        if not re.match(r'^[A-Z0-9]+$', clean):
            raise ValidationError("Le numéro de passeport ne doit contenir que des chiffres et des lettres majuscules.")
        # Un passeport standard comporte entre 7 et 10 caractères et contient généralement des lettres et des chiffres
        if len(clean) < 7 or len(clean) > 12:
            raise ValidationError("Le numéro de passeport doit comporter entre 7 et 10 caractères (ex: A1234567 ou 09AA12345).")
        has_letter = any(c.isalpha() for c in clean)
        has_digit = any(c.isdigit() for c in clean)
        if not (has_letter and has_digit):
            raise ValidationError("Le numéro de passeport doit comporter la série de lettres et les chiffres officiels (ex: A1234567).")

    elif id_type == 'RECEIPT':
        if not re.match(r'^[A-Z0-9]+$', clean):
            raise ValidationError("Le numéro de récépissé ne doit contenir que des chiffres et des lettres majuscules.")
        # Récépissé de CNI : délivré par la police / DGSN
        if len(clean) < 8 or len(clean) > 16:
            raise ValidationError("Le numéro de récépissé doit comporter entre 8 et 16 caractères (ex: REC20260987 ou 1029384756).")
            
    elif id_type == 'STUDENT':
        # Les cartes d'étudiant peuvent avoir des formats très variés (matricule universitaire, etc.)
        if len(clean) < 4 or len(clean) > 20:
            raise ValidationError("Le matricule d'étudiant/élève doit comporter entre 4 et 20 caractères.")

    return id_type, clean

