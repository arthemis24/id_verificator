import os
from celery import Celery

# Définit les variables d'environnement pour Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "id_verificator.settings")

# Création de l'application Celery
app = Celery("id_verificator")

# Charger les configurations Celery depuis settings.py
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-découverte des tâches dans tous les apps installés
app.autodiscover_tasks()

# Optionnel : Log plus détaillé
@app.task(bind=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
