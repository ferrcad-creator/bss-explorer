# Guide de déploiement BSS Explorer — Serveur OVH (Docker)

**Version :** 9.0.0  
**Public cible :** Administrateur système avec accès SSH au serveur  
**Durée estimée :** 30 à 60 minutes

---

## Table des matières

1. [Prérequis](#1-prérequis)
2. [Installation de Docker et Docker Compose](#2-installation-de-docker-et-docker-compose)
3. [Transfert des fichiers sur le serveur](#3-transfert-des-fichiers-sur-le-serveur)
4. [Configuration du fichier .env](#4-configuration-du-fichier-env)
5. [Configuration du domaine Nginx](#5-configuration-du-domaine-nginx)
6. [Premier lancement](#6-premier-lancement)
7. [Activation du HTTPS avec Let's Encrypt](#7-activation-du-https-avec-lets-encrypt)
8. [Vérification et accès](#8-vérification-et-accès)
9. [Commandes de maintenance](#9-commandes-de-maintenance)
10. [Mise à jour de l'application](#10-mise-à-jour-de-lapplication)
11. [Dépannage](#11-dépannage)

---

## 1. Prérequis

### Serveur OVH recommandé

| Caractéristique | Minimum | Recommandé |
|---|---|---|
| Distribution | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| RAM | 2 Go | 4 Go |
| Disque | 20 Go SSD | 40 Go SSD |
| CPU | 1 vCPU | 2 vCPU |
| Accès réseau | Port 80 + 443 ouverts | Port 80 + 443 ouverts |

### Nom de domaine (optionnel mais recommandé pour HTTPS)

Un nom de domaine pointant vers l'IP de votre serveur est nécessaire pour activer le HTTPS avec Let's Encrypt. Sans domaine, l'application fonctionne en HTTP sur l'IP du serveur.

### Accès SSH

```bash
ssh ubuntu@ADRESSE_IP_SERVEUR
# ou avec clé SSH :
ssh -i ~/.ssh/ma_cle.pem ubuntu@ADRESSE_IP_SERVEUR
```

---

## 2. Installation de Docker et Docker Compose

Connectez-vous en SSH à votre serveur et exécutez les commandes suivantes :

```bash
# Mise à jour du système
sudo apt-get update && sudo apt-get upgrade -y

# Installation des dépendances
sudo apt-get install -y ca-certificates curl gnupg lsb-release

# Ajout de la clé GPG officielle Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Ajout du dépôt Docker
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Installation de Docker et Docker Compose
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Permettre à l'utilisateur courant d'utiliser Docker sans sudo
sudo usermod -aG docker $USER
newgrp docker

# Vérification
docker --version
docker compose version
```

**Résultat attendu :**
```
Docker version 24.x.x, build ...
Docker Compose version v2.x.x
```

---

## 3. Transfert des fichiers sur le serveur

### Depuis votre machine locale (avec SCP)

```bash
# Transférer l'archive ZIP sur le serveur
scp bss_explorer_app.zip ubuntu@ADRESSE_IP_SERVEUR:/home/ubuntu/

# Se connecter en SSH
ssh ubuntu@ADRESSE_IP_SERVEUR

# Décompresser
cd /home/ubuntu
unzip bss_explorer_app.zip
cd bss_explorer_app
```

### Depuis GitHub (alternative)

```bash
# Sur le serveur
git clone https://github.com/ferrcad-creator/bss-explorer.git
cd bss-explorer
```

---

## 4. Configuration du fichier .env

Copiez le fichier d'exemple et renseignez vos valeurs :

```bash
cp configuration_exemple.txt .env
nano .env
```

**Variables obligatoires à renseigner :**

```bash
# ── Base de données PostgreSQL (Supabase) ─────────────────────────────────────
# Créez un projet gratuit sur https://supabase.com
# Récupérez l'URL de connexion dans : Settings > Database > Connection string > URI
DATABASE_URL=postgresql://postgres:MOT_DE_PASSE@db.XXXX.supabase.co:5432/postgres

# ── Sécurité ──────────────────────────────────────────────────────────────────
# Générez une clé aléatoire avec : python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=changez_cette_valeur_par_une_cle_aleatoire_de_64_caracteres

# ── Identifiants administrateur ───────────────────────────────────────────────
ADMIN_PASSWORD=votre_mot_de_passe_admin_securise
```

**Variables optionnelles :**

```bash
# Nom affiché dans l'interface
APP_TITLE=BSS Explorer — FERRAPD

# Port de l'application Streamlit (défaut : 8501)
APP_PORT=8501

# Port de l'API FastAPI (défaut : 8001)
API_PORT=8001

# Niveau de log (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO
```

**Enregistrez et fermez** : `Ctrl+X`, puis `Y`, puis `Entrée`.

---

## 5. Configuration du domaine Nginx

Éditez le fichier de configuration Nginx pour remplacer le domaine exemple par votre vrai domaine :

```bash
nano nginx/conf.d/bss-explorer.conf
```

Remplacez **toutes** les occurrences de `votre-domaine.com` par votre domaine réel :

```bash
# Rechercher et remplacer automatiquement
sed -i 's/votre-domaine.com/mon-vrai-domaine.com/g' nginx/conf.d/bss-explorer.conf
```

> **Sans domaine :** Si vous n'avez pas de domaine, remplacez `votre-domaine.com` par `_` pour accepter toutes les connexions sur l'IP du serveur.

---

## 6. Premier lancement

```bash
# Construction et démarrage de tous les services
docker compose up -d --build

# Vérifier que les conteneurs démarrent correctement
docker compose ps
```

**Résultat attendu :**

```
NAME          IMAGE         STATUS          PORTS
bss_app       bss_app       Up (healthy)    8501/tcp, 8001/tcp
bss_nginx     nginx:alpine  Up              0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
bss_certbot   certbot       Up
```

**Accès immédiat (HTTP) :**

```
http://ADRESSE_IP_SERVEUR
```

ou si vous avez configuré un domaine :

```
http://votre-domaine.com
```

---

## 7. Activation du HTTPS avec Let's Encrypt

> **Prérequis :** Votre domaine doit pointer vers l'IP du serveur (vérifiable avec `ping votre-domaine.com`).

### Étape 1 — Obtenir le certificat

```bash
# Remplacer votre-domaine.com et votre@email.com par vos vraies valeurs
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path /var/www/certbot \
    --email votre@email.com \
    --agree-tos \
    --no-eff-email \
    -d votre-domaine.com \
    -d www.votre-domaine.com
```

### Étape 2 — Activer la configuration HTTPS dans Nginx

```bash
nano nginx/conf.d/bss-explorer.conf
```

Dans ce fichier :
1. **Commentez** le bloc `location /` dans le serveur HTTP (ajoutez `#` devant chaque ligne du bloc)
2. **Décommentez** la ligne `return 301 https://$host$request_uri;`
3. **Décommentez** tout le bloc `server { listen 443 ssl ... }` (retirez tous les `#` au début de chaque ligne)

### Étape 3 — Recharger Nginx

```bash
docker compose exec nginx nginx -s reload
```

**Vérification :**

```
https://votre-domaine.com
```

Le navigateur doit afficher un cadenas vert. Le certificat se renouvelle automatiquement toutes les 12h.

---

## 8. Vérification et accès

### Vérifier que tout fonctionne

```bash
# Statut des conteneurs
docker compose ps

# Santé de l'API
curl http://localhost:8001/health

# Logs en temps réel
docker compose logs -f app
```

### URLs d'accès

| Service | URL |
|---|---|
| Interface Streamlit | `http://votre-domaine.com` ou `https://votre-domaine.com` |
| API FastAPI | `http://votre-domaine.com/api/health` |
| Documentation API | `http://votre-domaine.com/api/docs` |

---

## 9. Commandes de maintenance

### Afficher les logs

```bash
# Logs de l'application (temps réel)
docker compose logs -f app

# Logs Nginx
docker compose logs -f nginx

# Dernières 100 lignes
docker compose logs --tail=100 app
```

### Arrêter l'application

```bash
# Arrêt propre (les données sont conservées)
docker compose down

# Arrêt + suppression des volumes (ATTENTION : supprime les données !)
docker compose down -v
```

### Redémarrer un service

```bash
# Redémarrer uniquement l'application
docker compose restart app

# Redémarrer Nginx
docker compose restart nginx
```

### Vérifier l'espace disque

```bash
# Espace utilisé par Docker
docker system df

# Nettoyer les images inutilisées
docker system prune -f
```

### Sauvegarde manuelle de la base de données

Si vous utilisez Supabase, les sauvegardes sont gérées automatiquement. Pour une sauvegarde manuelle :

```bash
# Exporter toutes les sessions BSS en JSON
curl -s http://localhost:8001/sessions/export > backup_$(date +%Y%m%d).json
```

---

## 10. Mise à jour de l'application

```bash
# 1. Récupérer la nouvelle version
git pull origin main
# ou décompresser le nouveau ZIP

# 2. Reconstruire et redémarrer
docker compose up -d --build

# 3. Vérifier
docker compose ps
curl http://localhost:8001/health
```

---

## 11. Dépannage

### L'application ne démarre pas

```bash
# Vérifier les logs d'erreur
docker compose logs app

# Vérifier que le fichier .env est correct
cat .env

# Vérifier que les ports sont libres
sudo netstat -tlnp | grep -E "80|443|8501|8001"
```

### Erreur "Port already in use"

```bash
# Trouver le processus qui utilise le port 80
sudo lsof -i :80
# Tuer le processus (remplacer PID par le numéro trouvé)
sudo kill -9 PID
```

### Erreur de connexion à la base de données

1. Vérifiez que `DATABASE_URL` dans `.env` est correcte
2. Vérifiez que votre IP est autorisée dans Supabase (Settings > Database > Network)
3. Testez la connexion :
   ```bash
   docker compose exec app python3 -c "
   import psycopg2, os
   conn = psycopg2.connect(os.environ['DATABASE_URL'])
   print('Connexion OK')
   conn.close()
   "
   ```

### Pare-feu OVH — Ouvrir les ports 80 et 443

Sur le serveur Ubuntu :
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp    # SSH (ne pas oublier !)
sudo ufw enable
sudo ufw status
```

Dans le manager OVH (interface web) :
1. Connectez-vous sur https://www.ovhcloud.com/fr/
2. Allez dans **Serveurs > VPS** (ou Serveurs dédiés)
3. Cliquez sur votre serveur > **Réseau** > **Pare-feu**
4. Ajoutez des règles pour autoriser les ports 80 (HTTP) et 443 (HTTPS)

### Le certificat SSL ne se renouvelle pas

```bash
# Forcer le renouvellement
docker compose run --rm certbot renew --force-renewal

# Recharger Nginx
docker compose exec nginx nginx -s reload
```

### Streamlit affiche une page blanche

```bash
# Vérifier les logs Streamlit
docker compose logs app | grep -i "error\|warning"

# Redémarrer l'application
docker compose restart app
```

---

## Récapitulatif des commandes essentielles

| Action | Commande |
|---|---|
| Démarrer | `docker compose up -d --build` |
| Arrêter | `docker compose down` |
| Voir les logs | `docker compose logs -f app` |
| Statut | `docker compose ps` |
| Redémarrer | `docker compose restart app` |
| Mettre à jour | `git pull && docker compose up -d --build` |
| Santé API | `curl http://localhost:8001/health` |

---

*Guide rédigé pour BSS Explorer v9.0.0 — FERRAPD*
