# NetVision — Plateforme BI de Supervision Réseau

Plateforme décisionnelle de supervision et de gestion des incidents pour le NOC Huawei Maroc.
Projet de Fin d'Études 2026.

Stack : **Python / Flask · PostgreSQL (schéma en étoile) · JavaScript vanilla · Leaflet.js · Chart.js**

---

## 1. Structure du projet

```
netvision/
├── generate_data.py     # Génère les données simulées (CSV + JSON + schéma SQL)
├── fix_durees.sql       # Corrige les durées d'incidents aberrantes (>4h)
├── etl_pipeline.py      # Charge les données générées dans PostgreSQL
├── app.py               # API Flask (29 endpoints REST)
├── login.html           # Page de connexion / sélection de profil
├── noc_admin.html       # Interface Superviseur NOC
├── tech.html            # Interface Technicien Terrain
├── index.html           # Page de présentation
└── data/                # Généré automatiquement par generate_data.py
    ├── secteurs.csv / .json
    ├── equipements.csv / .json
    ├── techniciens.csv / .json
    ├── incidents.csv / .json
    ├── schema_dwh.sql
    └── rapport_etl.json
```

---

## 2. Prérequis

- **Python 3.10+**
- **PostgreSQL 14+** installé et démarré
- Une base de données nommée `bi_telecom` (créée vide au préalable)

### Dépendances Python

```bash
pip install flask flask-cors psycopg2-binary
```

---

## 3. Configuration de la base de données

Deux fichiers contiennent la configuration de connexion à PostgreSQL — **vérifiez qu'elles correspondent à votre installation locale avant de lancer quoi que ce soit** :

| Fichier | Variable | Valeur par défaut |
|---|---|---|
| `app.py` | `DB_CONFIG` (ligne ~32) | `host=127.0.0.1`, **port=5433**, `dbname=bi_telecom`, `user=postgres`, `password=admin` |
| `etl_pipeline.py` | `DB_CONFIG` (variables d'environnement) | `host=localhost`, **port=5432**, `dbname=bi_telecom`, `user=postgres`, `password=postgres` |

> ⚠️ **Attention au port** : `app.py` pointe par défaut sur le port `5433` et `etl_pipeline.py` sur `5432`. Adaptez les deux fichiers (ou vos variables d'environnement) pour qu'ils ciblent le **même** serveur PostgreSQL.

`etl_pipeline.py` peut aussi être configuré sans toucher au code, via variables d'environnement :

```bash
export DB_HOST=127.0.0.1
export DB_PORT=5433
export DB_NAME=bi_telecom
export DB_USER=postgres
export DB_PASSWORD=admin
```

---

## 4. Installation — ordre d'exécution

L'ordre ci-dessous est **obligatoire** : chaque étape dépend de la précédente.

### Étape 1 — Générer les données simulées

```bash
python generate_data.py
```

Crée le dossier `data/` contenant :
- `secteurs.csv`, `equipements.csv`, `techniciens.csv`, `incidents.csv` (+ versions `.json`)
- `schema_dwh.sql` — script de création des tables du Data Warehouse (3 dimensions + 1 table de faits + table d'audit)

À ce stade, tous les incidents sont générés avec le statut `OUVERT` et **aucun ticket n'existe encore** (les tickets ne sont créés que via le dispatch depuis l'interface NOC Admin).

### Étape 2 — Corriger les durées aberrantes (`fix_durees.sql`)

Certaines durées estimées générées aléatoirement à l'étape 1 peuvent dépasser 4 heures, ce qui n'est pas cohérent avec les plages réalistes attendues par le Gantt et la logique métier du dispatch. **Ce script doit être exécuté après la génération des données et avant le chargement en base**, directement sur le fichier SQL généré ou après un premier chargement (voir note ci-dessous).

Deux façons de l'exécuter :

**Option A — via pgAdmin (recommandé)**
1. Ouvrez pgAdmin et connectez-vous à la base `bi_telecom`.
2. Ouvrez l'éditeur de requêtes (Query Tool).
3. Chargez et exécutez `fix_durees.sql` dans son intégralité.

**Option B — via la ligne de commande `psql`**
```bash
psql -h 127.0.0.1 -p 5433 -U postgres -d bi_telecom -f fix_durees.sql
```

> ℹ️ **Note d'ordre** : `fix_durees.sql` opère directement sur les tables `fact_incidents` et `fact_tickets` en base PostgreSQL — il doit donc être exécuté **après** l'étape 3 (chargement ETL), une fois que les données sont effectivement en base. Si vous préférez corriger les durées avant tout chargement, vous pouvez aussi éditer les plages directement dans `generate_data.py` (variable de génération des durées) avant de relancer l'étape 1. La méthode recommandée pour ce projet est : **générer → charger (ETL) → corriger avec `fix_durees.sql`**.

Le script :
1. Affiche un état des lieux des durées par type d'incident (min/max/moyenne).
2. Ramène toute durée estimée supérieure à 240 minutes (4h) à une plage réaliste et cohérente par type de panne (ex. panne complète : 3h–4h, alarme réseau : 30min–1h).
3. Synchronise les tickets déjà dispatchés avec la nouvelle durée de leur incident — **sans jamais modifier l'historique des tickets déjà clôturés** (COMPLETE/INCOMPLET).
4. Réaffiche l'état des lieux après correction et vérifie qu'il ne reste aucune durée active anormale.

### Étape 3 — Charger les données en base (ETL)

```bash
python etl_pipeline.py db
```

Ce mode :
1. Exécute `data/schema_dwh.sql` pour créer (ou vérifier l'existence de) toutes les tables.
2. Charge dans l'ordre des contraintes de clés étrangères : `dim_secteur` → `dim_equipement` → `dim_technicien` → `dim_temps` → `fact_incidents`.
3. Génère un rapport de synthèse dans `data/rapport_etl.json`.

> Lancé sans argument (`python etl_pipeline.py`), le pipeline s'exécute en mode `file` (extraction/transformation uniquement, sans connexion à PostgreSQL) — utile pour valider les données avant un chargement réel.

**Si vous suivez l'ordre recommandé (générer → charger → corriger), exécutez maintenant `fix_durees.sql` (étape 2) sur la base fraîchement chargée.**

### Étape 4 — Lancer l'API Flask

```bash
python app.py
```

Le serveur démarre sur `http://localhost:5000`. La console affiche la configuration de connexion à la base utilisée et vérifie immédiatement que la connexion fonctionne.

### Étape 5 — Ouvrir les interfaces

Ouvrez simplement `login.html` dans un navigateur (double-clic ou `file://`) — aucun serveur web n'est nécessaire pour le frontend, qui communique avec l'API Flask via des appels REST asynchrones.

- **NOC Admin** → tableau de bord, carte interactive, dispatch, planning Gantt, gestion des incidents bloqués.
- **Technicien Terrain** → ticket actif, mise à jour de statut, gestion de la pause, clôture d'intervention.

---

## 5. Résumé de l'ordre complet

```bash
# 1. Générer les données
python generate_data.py

# 2. Charger en base
python etl_pipeline.py db

# 3. Corriger les durées aberrantes (après chargement)
psql -h 127.0.0.1 -p 5433 -U postgres -d bi_telecom -f fix_durees.sql
# (ou exécution manuelle via pgAdmin)

# 4. Lancer l'API
python app.py

# 5. Ouvrir login.html dans le navigateur
```

---

## 6. Dépannage rapide

| Symptôme | Cause probable |
|---|---|
| `app.py` ne se connecte pas à PostgreSQL | Port ou mot de passe incorrect dans `DB_CONFIG` (ligne ~32 de `app.py`) |
| `etl_pipeline.py` lève `psycopg2 non installé` | `pip install psycopg2-binary` |
| Le Gantt affiche des barres de durée incohérentes (>4h) | `fix_durees.sql` n'a pas été exécuté, ou exécuté avant le chargement ETL |
| Les interfaces affichent "Impossible de contacter l'API Flask" | `app.py` n'est pas lancé, ou tourne sur un port différent de celui attendu (`http://localhost:5000`) |
| Erreurs CORS dans la console navigateur | Vérifier que `flask-cors` est bien installé et que `CORS(app, supports_credentials=True)` est actif dans `app.py` |

---

*NetVision — PFE 2026 · Network Operations Center · Huawei Technologies Morocco*
