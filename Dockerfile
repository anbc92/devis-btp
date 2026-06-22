FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependances (gunicorn et Pillow sont dans requirements.txt)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code applicatif
COPY . .

# Donnees persistantes (base SQLite, logos). Le volume est gere cote Railway
# (monte sur /app/data via le dashboard) : NE PAS utiliser l'instruction Docker
# VOLUME, que le builder Railway refuse ("docker VOLUME is not supported, use
# Railway Volumes") -> echec du build. Le conteneur tourne en root (pas de
# directive USER) pour pouvoir ecrire sur le volume. mkdir garantit l'existence
# du dossier hors volume monte (build local, etc.).
RUN mkdir -p /app/data/uploads
ENV DB_PATH=/app/data/devis.db
# Logos uploades sous le volume (MEDIA_ROOT/uploads) pour survivre aux deploys.
ENV MEDIA_ROOT=/app/data

EXPOSE 8000

# SECRET_KEY / SESSION_COOKIE_SECURE / MAIL_* a fournir via l'environnement.
# 2 workers gunicorn ; ajuster selon la charge.
# Forme shell (pas exec) pour que $PORT injecte par Railway soit interprete ;
# repli sur 8000 en local. Railway route le trafic vers $PORT, pas un port fixe.
CMD gunicorn --bind "0.0.0.0:${PORT:-8000}" --workers 2 app:app
