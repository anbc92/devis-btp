FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Code applicatif
COPY . .

# Donnees persistantes (base SQLite, logos) montees en volume
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/devis.db

EXPOSE 8000

# SECRET_KEY / SESSION_COOKIE_SECURE / MAIL_* a fournir via l'environnement.
# 2 workers gunicorn ; ajuster selon la charge.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
