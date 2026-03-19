# Dockerfile
FROM python:3.12-slim

# Définir le répertoire de travail
WORKDIR /app

# Installer les dépendances système nécessaires
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    libffi-dev \
    libpq-dev \
    git \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*


# Copier les fichiers Poetry et installer les dépendances
COPY pyproject.toml poetry.lock* /app/

RUN pip install --upgrade pip \
    && pip install poetry \
    && pip install --no-cache-dir tf-keras tensorflow \
    && poetry config virtualenvs.create false \
    && poetry install --no-root

# Copier tout le code dans le container
COPY . /app

# Ajouter src/ au PYTHONPATH pour que Django et Celery trouvent id_verificator
ENV PYTHONPATH=/app/src

# Commande par défaut pour lancer Django
CMD ["python", "manage.py", "runserver", "0.0.0.0:8001"]
