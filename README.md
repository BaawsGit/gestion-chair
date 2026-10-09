# Gestion Chair · Ferme Avicole de Poulets de Chair

Application web locale complète et robuste développée avec Django 5.2, PostgreSQL et Bootstrap 5 pour le pilotage d'une ferme avicole de poulets de chair (cycles de ~45 jours par bande).

---

## 1. État du Projet & Données Initialisées

- **Cycle de production & Lots** : Suivi par lot (« Lot #1 - Octobre 2026 »), date d'arrivée le 07/10/2026, durée cible de 45 jours.
- **Indicateurs en temps réel** : Âge du lot (J-X / 45), effectif vivant en temps réel, taux de mortalité cumulé (%).
- **Dépenses préliminaires & variables** :
  - *Investissements fixes (amortissement au prorata)* : Rénovation poulailler, tôles, matériel d'élevage (mangeoires, abreuvoirs).
  - *Charges variables du lot* : Poussins d'un jour, aliment démarrage, produits vétérinaires & vaccins, litière (copeaux).
- **Mortalité** : Enregistrement quotidien avec causes présumées (1 décès initial au 07/10/2026).
- **Stocks & Alimentation** : Suivi des types d'aliment (Démarrage, Croissance, Finition), conversion automatique sacs/kilogrammes, alertes de niveau.
- **Calendrier prophylactique** : Génération automatique des dates de soins (J1-J5 Vitamines, J10 Gumboro, J14 Newcastle) avec validation rapide en 1 clic.
- **Sorties & Ventes** : Enregistrement des ventes de poulets, calcul du poids moyen, gestion des statuts de règlement (Payé, Crédit).
- **Clôture & Bilan de bande** : Calcul du coût de revient unitaire par poulet produit, marge nette par sujet, archivage propre et conservation de l'historique pour lancer le Lot #2.

---

## 2. Démarrage Rapide

### Prérequis
- Python 3.10 ou 3.11+
- PostgreSQL (installé localement ou via Docker)
- PowerShell (Windows) ou Bash

### Installation pas-à-pas

1. **Cloner ou ouvrir le projet** :
   ```powershell
   cd "d:\PROJETS\Gestion Chair"
   ```

2. **Créer et activer l'environnement virtuel** :
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

3. **Configurer les variables d'environnement** :
   Le fichier `.env` est prêt à l'emploi. Vous pouvez choisir d'utiliser PostgreSQL (par défaut) ou SQLite (pour tester immédiatement sans serveur SQL) via la variable `DB_ENGINE` :
   ```dotenv
   DB_ENGINE=postgresql
   POSTGRES_DB=poulailler
   POSTGRES_USER=poulailler
   POSTGRES_PASSWORD=poulailler_local
   POSTGRES_HOST=127.0.0.1
   POSTGRES_PORT=5432
   ```

4. **Initialiser PostgreSQL** (si `DB_ENGINE=postgresql`) :
   - *Option A (PostgreSQL natif sous Windows)* :
     Exécutez le script d'initialisation fourni avec le superutilisateur `postgres` :
     ```powershell
     psql -U postgres -f init_postgres.sql
     ```
   - *Option B (Docker Desktop)* :
     ```powershell
     docker compose up -d db
     ```

5. **Appliquer les migrations** :
   ```powershell
   python manage.py makemigrations poulailler_core
   python manage.py migrate
   ```

6. **Initialiser la ferme et les données réelles** :
   ```powershell
   python manage.py setup_farm --with-initial-expenses --no-input
   ```
   *(Pour personnaliser manuellement les montants ou les quantités, omettez `--no-input` pour le mode interactif).*

7. **Créer un compte administrateur (si non existant)** :
   ```powershell
   python manage.py createsuperuser
   ```
   *(Un compte test `admin` / `admin1234` est préconfiguré pour la prise en main rapide).*

8. **Lancer le serveur de développement** :
   ```powershell
   python manage.py runserver
   ```
   Accédez à :
   - Interface web : **http://127.0.0.1:8000/**
   - Backoffice Django : **http://127.0.0.1:8000/admin/**

---

## 3. Structure du Projet

```
Gestion Chair/
├── config/
│   ├── settings.py           # Configuration Django, liaison PostgreSQL & switch DB_ENGINE
│   ├── urls.py               # Routage central & authentification
│   ├── wsgi.py / asgi.py
├── poulailler_core/
│   ├── models.py             # Batch, Expense, Mortality, FeedInventory, FeedConsumption, ProphylaxisProgram, TreatmentLog, Sale
│   ├── views.py              # Dashboard, Journal rapide, Soins, Dépenses, Ventes, Stocks, Clôture
│   ├── forms.py              # Formulaires stylisés Bootstrap 5 (Journal rapide, Lots, Dépenses, Ventes)
│   ├── urls.py               # Routes de l'application avicole
│   ├── admin.py              # Administration avec inlines, filtres par date et recherches
│   ├── signals.py            # Synchronisation automatique du calendrier de soins
│   ├── finance.py            # Calculs de marge, amortissement des investissements et projection
│   ├── management/commands/
│   │   └── setup_farm.py     # Initialisation des catégories, protocoles, lot #1 et dépenses
│   ├── tests.py              # 10 tests automatisés couvrant tous les parcours et calculs
│   └── migrations/           # Historique des migrations de base de données
├── templates/
│   ├── base.html             # Layout responsive DM Sans / Manrope avec navigation moderne
│   ├── registration/         # Écran de connexion soigné
│   └── poulailler_core/
│       ├── dashboard.html    # KPI en direct, graphiques Chart.js (mortalité, dépenses), alertes soins
│       ├── daily_entry.html  # Formulaire de saisie en 30 secondes (morts + aliments + soins)
│       ├── treatments.html   # Calendrier prophylactique complet avec bouton de validation rapide
│       ├── expenses.html     # Livre des dépenses avec totaux et filtres
│       ├── sales.html        # Suivi des ventes, clients et paiements (crédit/payé)
│       ├── stocks.html       # État des réserves d'aliment et historique de consommation
│       ├── batches.html      # Historique de toutes les bandes de production
│       ├── batch_report.html # Bilan financier et KPI avicoles de clôture
│       └── form_page.html    # Modèle générique pour formulaires
├── static/css/app.css        # Feuille de styles moderne (thème vert végétal, cartes KPI, responsive)
├── init_postgres.sql         # Script SQL de création de rôle et base PostgreSQL
├── docker-compose.yml        # Service PostgreSQL 16 containerisé
├── requirements.txt          # Django, psycopg (v3), python-dotenv
└── manage.py
```

---

## 4. Tests Automatisés

Pour lancer la suite de tests unitaires et fonctionnels :
```powershell
$env:DB_ENGINE="sqlite3"; python manage.py test
```
Tous les 10 tests valident l'intégrité des calculs d'âge, du stock disponible, de la mortalité, des conversions d'aliments, du calendrier de soins, de la clôture et de l'affichage des pages.
