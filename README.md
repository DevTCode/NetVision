# NetVision — Plateforme BI Télécom Huawei Maroc

> Plateforme décisionnelle de supervision réseau et de gestion des incidents,
> développée dans le cadre d'un projet de fin d'études (PFE 2026) au sein du
> Network Operations Center (NOC) de Huawei Maroc.

NetVision centralise en temps réel les opérations de maintenance du réseau
télécom marocain : **10 secteurs géographiques**, **120 équipements Huawei**
et **30 techniciens terrain**. Elle couvre tout le cycle de vie d'un incident
— détection, dispatch intelligent, intervention terrain, clôture automatique
et traçabilité complète.

---

## 1. Fonctionnalités

### Tableau de bord analytique
- 7 indicateurs clés (incidents critiques, MTTR réel, taux de résolution,
  techniciens disponibles, durée estimée moyenne, etc.)
- 8 graphiques interactifs (distribution horaire, répartition par sévérité,
  analyse par secteur/réseau, évolution sur 7 jours, performance technicien)

### Carte interactive géolocalisée
- Équipements réseau, incidents actifs (par sévérité) et techniciens terrain
  affichés en temps réel sur le territoire marocain (Leaflet.js)
- Filtrage dynamique + panneau de dispatch intégré

### Dispatch intelligent
- Assignation automatique du technicien le plus pertinent selon :
  proximité géographique, appartenance au même secteur, note de
  performance historique
- Gestion de la reprise au lendemain (J+1, 09h00) pour toute intervention
  dont la fin estimée dépasse 18h00

### Planning Gantt journalier
- Visualisation des interventions de tous les techniciens
- Navigation dans l'historique (jour par jour)
- Détection des retards en temps réel + notifications automatiques
- Export Excel/CSV

### Interface technicien terrain
- Mise à jour séquentielle du statut d'intervention en 4 étapes
  (`EN_ROUTE → SUR_SITE → EN_COURS → COMPLETE | INCOMPLET`)
- Pause terrain avec décompte visuel (statut `EN_PAUSE`), une seule pause
  autorisée par ticket — la durée de pause s'ajoute à la durée estimée
- Notifications sonores et visuelles
- Clôture avec notation ou saisie de cause d'échec

### Clôture automatique & redispatch
- Détection toutes les 30 secondes des tickets dont la durée estimée est
  dépassée → fermeture automatique, libération du technicien
- Incidents bloqués (`INCOMPLET`) visibles sur la carte avec mécanisme de
  redispatch conditionnel soumis à validation du superviseur NOC

### Traçabilité complète
- Historique intégral de chaque ticket et de chaque transition de statut
  dans `ticket_historique` (audit trail persistant)
- Timeline interactive consultable depuis le Gantt

### Accès aux interfaces
- `login.html` propose deux profils : **NOC Admin** et **Technicien
  Terrain**, chacun menant vers son interface dédiée. Côté technicien, un
  sélecteur permet ensuite de choisir son profil parmi les 30 techniciens.

---

## 2. Stack technique

| Couche | Technologies |
|---|---|
| Backend | Python 3.11 · Flask · Flask-CORS · psycopg2 |
| Base de données | PostgreSQL 15 · Schéma en étoile · Data Warehouse |
| Frontend | HTML5 · CSS3 · JavaScript vanilla |
| Cartographie | Leaflet.js 1.9.4 · CartoCDN |
| Visualisations | Chart.js 4.4.1 |
| Icônes | Bootstrap Icons 1.11.3 |
| Outils | VS Code · Postman · Git · Google Chrome |

---

## 3. Architecture générale

```
generate_data.py
   │  génère secteurs / équipements / techniciens / incidents
   │  (CSV + JSON) et le schéma SQL (schema_dwh.sql)
   ▼
data/  (CSV, JSON, schema_dwh.sql)
   │
   ▼
etl_pipeline.py
   │  extract → transform → load (fichier .sql ou insertion directe DB)
   ▼
PostgreSQL  (base "bi_telecom" — schéma en étoile)
   │
   ▼
app.py  (API Flask — http://localhost:5000)
   │
   ├──► login.html      → choix de profil (Admin / Technicien)
   ├──► noc_admin.html  → Dashboard, Carte, Incidents, Techniciens, Gantt
   └──► tech.html       → Interface technicien terrain
```

---

## 4. Structure du projet

```
.
├── generate_data.py     # Générateur de données simulées + schéma SQL
├── etl_pipeline.py       # Pipeline ETL (extract / transform / load)
├── app.py                 # API Flask (backend)
├── login.html             # Page de connexion (choix de profil)
├── noc_admin.html         # Interface NOC Admin
├── tech.html              # Interface technicien terrain
└── data/                  # Sorties générées
    ├── secteurs.csv / .json
    ├── equipements.csv / .json
    ├── techniciens.csv / .json
    ├── incidents.csv / .json
    └── schema_dwh.sql
```

---

## 5. Modèle de données (schéma en étoile)

**Dimensions**
- `dim_secteur` — 10 secteurs géographiques (région, coordonnées)
- `dim_equipement` — 120 équipements Huawei (type, modèle, statut, position)
- `dim_technicien` — 30 techniciens (compétences, secteur, statut, horaires
  09h-18h, note de performance)
- `dim_temps` — calendrier (jour, semaine, mois, trimestre)

**Faits**
- `fact_incidents` — incidents détectés (statut `OUVERT → EN_COURS →
  RESOLU/FERME`, durée estimée, sévérité, géolocalisation)
- `fact_tickets` — tickets d'intervention créés au dispatch (statut terrain,
  timeline complète, pause terrain `pause_debut` / `duree_pause_min`)
- `ticket_historique` — audit trail de chaque transition de statut

**Vues**
- `v_kpi_global`, `v_incidents_par_secteur`, `v_performance_techniciens`,
  `v_gantt_aujourdhui`

---

## 6. Étapes de réalisation / Mise en route

### Prérequis
- Python 3.11+
- PostgreSQL 15
- `pip install flask flask-cors psycopg2-binary`

### Étape 1 — Générer les données et le schéma
```bash
python3 generate_data.py
```
Crée le dossier `data/` avec les CSV/JSON (secteurs, équipements,
techniciens, incidents — tous `OUVERT`, aucun ticket pré-assigné) et
`schema_dwh.sql`.

### Étape 2 — Créer la base PostgreSQL
```sql
CREATE DATABASE bi_telecom;
```

### Étape 3 — Charger les données via l'ETL
```bash
# Génère un script SQL d'insertion (data/dwh_inserts.sql)
python3 etl_pipeline.py file

# OU insertion directe en base (variables d'env DB_HOST, DB_USER, ... ou défauts)
python3 etl_pipeline.py db
```
L'ETL crée les tables depuis `schema_dwh.sql`, transforme les données
(calcul de `duree_estimee_min` si absente, forçage du statut `OUVERT`, etc.)
et produit un rapport `data/rapport_etl.json`.

### Étape 4 — Configurer et lancer l'API
Dans `app.py`, vérifier/adapter `DB_CONFIG` (utilisateur, mot de passe,
nom de la base), puis :
```bash
python3 app.py
```
L'API démarre sur `http://localhost:5000` (vérifie la connexion PostgreSQL
au lancement).

### Étape 5 — Ouvrir l'interface
Ouvrir `login.html` dans le navigateur, choisir un profil :
- **NOC Admin** → `noc_admin.html` (dashboard, carte, dispatch, Gantt)
- **Technicien Terrain** → `tech.html` (sélectionner un technicien dans la
  liste, puis suivre les interventions assignées)

---

## 7. Endpoints API principaux

| Catégorie | Endpoint | Description |
|---|---|---|
| Santé | `GET /api/health` | Vérification connexion DB |
| KPI | `GET /api/kpi` | Indicateurs globaux du dashboard |
| Incidents | `GET /api/incidents` | Liste paginée/filtrée des incidents |
| Incidents | `GET /api/incidents/par-secteur` `/par-reseau` `/par-heure` `/evolution` | Données pour les graphiques |
| Incidents | `GET /api/incidents/escalade` | Incidents critiques/majeurs non assignés depuis +30 min |
| Incidents | `POST /api/incidents/<id>/debloquer` | Remet un incident `INCOMPLET` à `OUVERT` (redispatch) |
| Carte | `GET /api/carte` | Secteurs, équipements, incidents, techniciens géolocalisés |
| Dispatch | `GET /api/dispatch/<incident_id>` | Liste des techniciens classés (proximité, secteur, score) |
| Dispatch | `POST /api/dispatch/assigner` | Crée le ticket et assigne le technicien |
| Dispatch | `POST /api/dispatch/assigner-j1` | Confirme une assignation reportée au lendemain |
| Tickets | `GET /api/tickets` `/api/tickets/<id>` | Liste / détail d'un ticket |
| Tickets | `PATCH /api/tickets/<id>/statut` | Mise à jour du statut terrain (4 étapes) |
| Tickets | `POST /api/tickets/<id>/pause` | Démarrer/terminer la pause terrain (décompte) |
| Tickets | `POST /api/tickets/<id>/forcer-cloture` | Clôture manuelle (admin) |
| Tickets | `POST /api/tickets/fermer-retards` | Clôture automatique des tickets en retard (appelé toutes les 30s) |
| Gantt | `GET /api/gantt?date=YYYY-MM-DD` | Planning journalier par technicien |
| Techniciens | `GET /api/techniciens` `/disponibles` `/<id>` | Liste, disponibilité, détail/stats |
| Performance | `GET /api/performance` `/api/performance/techniciens` | Statistiques de performance |

---

## 8. Cycle de vie d'un ticket

```
OUVERT (incident)
   │  dispatch admin
   ▼
ASSIGNE ──► EN_ROUTE ──► SUR_SITE ──► EN_COURS ──┬──► COMPLETE
                                        │          │
                                        ▼          └──► INCOMPLET (cause obligatoire)
                                     EN_PAUSE             │
                                  (1x max,           debloquer()
                                   décompte)               │
                                        │                   ▼
                                        └──► reprise EN_COURS  OUVERT (redispatch)
```

Toute transition est journalisée dans `ticket_historique` (statut avant/
après, auteur `ADMIN`/`TECHNICIEN`/`SYSTEME`, horodatage, commentaire).

---

## 9. Authentification

L'authentification multi-comptes (table `dim_users`, mots de passe) a été
volontairement écartée du périmètre de ce PFE — cadrage validé avec
l'encadrant, une implémentation complète étant prévue dans une phase
ultérieure par l'équipe de développement. À la place, `login.html` propose
un choix de profil simple (Admin / Technicien) stocké en `sessionStorage`,
et `tech.html` permet de sélectionner le technicien à incarner.

---

## 10. Limites connues & perspectives

- Pas d'authentification individuelle par technicien (cf. section 9)
- Connexion PostgreSQL en dur dans `app.py` (`DB_CONFIG`) — à externaliser
  en variables d'environnement pour un déploiement réel
- Données simulées (`generate_data.py`, seed fixe) — à remplacer par une
  intégration avec les systèmes de supervision réels (U2000, etc.)
- Pistes d'évolution : notifications push, application mobile technicien,
  prévision de charge par secteur (ML)

---

## 11. Contexte

Projet de fin d'études (PFE) — 2026
Network Operations Center, Huawei Maroc
