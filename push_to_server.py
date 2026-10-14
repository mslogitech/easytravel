import os
import tarfile
import paramiko
import sys
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

# Assurez-vous que l'affichage console supporte les caractères spéciaux
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOST = os.environ.get("SERVER_IP", "169.58.52.142")
USER = os.environ.get("SERVER_USER", "root")
PWD = os.environ.get("SERVER_PASSWORD")
LOCAL_DIR = str(Path(__file__).resolve().parent)

if not PWD:
    print("Erreur : Le mot de passe du serveur (SERVER_PASSWORD) n'est pas défini dans le fichier .env !")
    sys.exit(1)
ARCHIVE_PATH = os.path.join(LOCAL_DIR, "easytravel_sync.tar.gz")

def exclude_filter(tarinfo):
    name = tarinfo.name.replace('\\', '/')
    # Ignorer les dossiers et fichiers inutiles pour le serveur
    for ign in ['venv', '__pycache__', '.git', 'db.sqlite3', '.gemini', '.pytest_cache', 'scratch', '.env']:
        if ign in name.split('/'):
            return None
    if name.endswith('.pyc') or name.endswith('.log') or name.endswith('.tar.gz'):
        return None
    return tarinfo

print("=== DEPLOIEMENT VERS LE SERVEUR ===")
print("1. Création de l'archive du projet local...")
with tarfile.open(ARCHIVE_PATH, "w:gz") as tar:
    tar.add(LOCAL_DIR, arcname="easytravel", filter=exclude_filter)

size_mb = os.path.getsize(ARCHIVE_PATH) / (1024 * 1024)
print(f"-> Archive créée avec succès ({size_mb:.2f} MB)")

print("\n2. Connexion SSH au serveur (169.58.52.142)...")
try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=HOST, port=22, username=USER, password=PWD, timeout=15)
except Exception as e:
    print(f"Erreur de connexion SSH : {e}")
    sys.exit(1)

print("\n3. Envoi de l'archive via SFTP...")
sftp = ssh.open_sftp()
sftp.put(ARCHIVE_PATH, "/tmp/easytravel_sync.tar.gz")
sftp.close()
print("-> Envoi terminé !")

print("\n4. Déploiement et redémarrage des services sur le serveur...")
cmd_deploy = """
echo "Extraction des fichiers..."
tar -xzf /tmp/easytravel_sync.tar.gz -C /var/www/
cp -r /var/www/easytravel/easytravel/* /var/www/easytravel/ 2>/dev/null || true
rm -rf /var/www/easytravel/easytravel
rm -f /tmp/easytravel_sync.tar.gz

echo "Mise à jour des permissions..."
chown -R root:www-data /var/www/easytravel
chmod -R 755 /var/www/easytravel
mkdir -p /var/www/easytravel/logs
chmod -R 775 /var/www/easytravel/media /var/www/easytravel/static /var/www/easytravel/logs || true

echo "Application des migrations de base de données..."
cd /var/www/easytravel
source venv/bin/activate
python manage.py migrate

echo "Collecte des fichiers statiques..."
python manage.py collectstatic --noinput

echo "Redémarrage de Gunicorn (Django)..."
systemctl restart easytravel

echo "Redémarrage de Nginx..."
systemctl reload nginx

echo "Déblocage des IP sur Fail2ban..."
fail2ban-client unban --all 2>/dev/null || true

echo "Déploiement terminé avec succès !"
"""
stdin, stdout, stderr = ssh.exec_command(cmd_deploy)
out = stdout.read().decode('utf-8')
err = stderr.read().decode('utf-8')

print("\n--- Logs du Serveur ---")
print(out)
if err:
    print("Avertissements/Erreurs:")
    print(err)

ssh.close()

# Nettoyage de l'archive locale
if os.path.exists(ARCHIVE_PATH):
    os.remove(ARCHIVE_PATH)

print("=== TERMINE ! VOS MODIFICATIONS SONT MAINTENANT EN LIGNE ===")
