"""
generate_data.py — v4
Générateur de données simulées — Plateforme BI Télécom Huawei
PFE 2026

Changements v4 :
  - Horaires techniciens corrigés : 09h00-18h00 (au lieu de 08h-17h),
    alignés sur DAY_START/DAY_END et le Gantt de app.py
  - Pause déjeuner fixe (dim_technicien.pause_debut/pause_fin, 12h-13h30
    ou 14h le vendredi) SUPPRIMÉE — il ne reste que la pause terrain
    prise par le technicien (statut_terrain='EN_PAUSE')
  - Ajout colonnes fact_tickets.pause_debut (TIMESTAMP) et
    duree_pause_min (INT) — pause terrain avec décompte visuel
    (POST /api/tickets/<id>/pause), absentes du schéma v3
  - Table dim_users SUPPRIMÉE (jamais peuplée ni utilisée par app.py) —
    connexion simplifiée via login.html (choix de profil Admin/Technicien)

Changements v3 :
  - Les incidents générés sont TOUS statut OUVERT (pas encore traités)
  - Les tickets ne sont PAS pré-générés → créés uniquement via dispatch admin
  - Les techniciens sont TOUS DISPONIBLE au départ (ticket_actif_id = NULL)
  - La date de référence est AUJOURD'HUI (date du jour réel)
  - Ajout table dim_users pour l'authentification admin / technicien
  - Ajout table ticket_historique pour tracer chaque changement de statut
  - Ajout colonne duree_estimee_min dans fact_incidents (calculée selon type)
  - Ajout colonne code_incident (unique, format INC-YYYYMM-XXXX)
"""

import random
import json
import csv
import os
from datetime import datetime, timedelta

random.seed(42)

TODAY = datetime.now()

# ═══════════════════════════════════════════════════════════════
# RÉFÉRENTIELS
# ═══════════════════════════════════════════════════════════════

SECTEURS = [
    {"id": 1,  "nom": "Grand Casablanca", "region": "Casablanca-Settat",          "lat": 33.5731, "lng": -7.5898},
    {"id": 2,  "nom": "Rabat-Salé",       "region": "Rabat-Salé-Kénitra",         "lat": 34.0209, "lng": -6.8416},
    {"id": 3,  "nom": "Marrakech",        "region": "Marrakech-Safi",             "lat": 31.6295, "lng": -7.9811},
    {"id": 4,  "nom": "Fès",              "region": "Fès-Meknès",                 "lat": 34.0181, "lng": -5.0078},
    {"id": 5,  "nom": "Tanger",           "region": "Tanger-Tétouan-Al Hoceima", "lat": 35.7595, "lng": -5.8340},
    {"id": 6,  "nom": "Agadir",           "region": "Souss-Massa",               "lat": 30.4278, "lng": -9.5981},
    {"id": 7,  "nom": "Meknès",           "region": "Fès-Meknès",                "lat": 33.8935, "lng": -5.5547},
    {"id": 8,  "nom": "Oujda",            "region": "Oriental",                  "lat": 34.6867, "lng": -1.9114},
    {"id": 9,  "nom": "Kenitra",          "region": "Rabat-Salé-Kénitra",        "lat": 34.2610, "lng": -6.5802},
    {"id": 10, "nom": "Tétouan",          "region": "Tanger-Tétouan-Al Hoceima", "lat": 35.5785, "lng": -5.3684},
]

TYPES_EQUIPEMENT = [
    ("BTS_2G",  "Station de base 2G",     ["RRU3908", "BBU5900"]),
    ("BTS_3G",  "Station de base 3G",     ["AAU5614", "BBU5900"]),
    ("BTS_4G",  "Station de base 4G LTE", ["AAU5614", "BBU5900"]),
    ("BTS_5G",  "Station de base 5G NR",  ["AAU5614", "BBU5900"]),
    ("ROUTEUR", "Routeur backbone",        ["NE40E-X8", "ATN910C"]),
    ("SWITCH",  "Switch distribution",    ["ATN910C", "NE40E-X8"]),
    ("OLT",     "Optical Line Terminal",  ["BBU5900", "NE40E-X8"]),
    ("ANTENNE", "Antenne sectorielle",    ["RRU3908", "AAU5614"]),
]

# (code, libelle, severite, duree_min_estimee, duree_max_estimee)
TYPES_INCIDENT = [
    ("PANNE_COMPLETE",  "Panne totale équipement",      "CRITIQUE", 120, 480),
    ("DEGRADATION",     "Dégradation performances",     "MAJEUR",    60, 240),
    ("ALARME_RESEAU",   "Alarme réseau détectée",       "MINEUR",    30, 120),
    ("COUPURE_FIBRE",   "Coupure fibre optique",        "CRITIQUE", 180, 600),
    ("SURCHARGE",       "Surcharge trafic",             "MAJEUR",    45, 180),
    ("INTERFERENCE",    "Interférence signal",          "MINEUR",    20,  90),
    ("PANNE_ALIM",      "Panne alimentation",           "CRITIQUE", 240, 720),
    ("MISE_A_JOUR",     "Echec mise à jour firmware",   "MINEUR",    60, 180),
]

COMPETENCES = ["2G/3G", "4G/LTE", "5G", "Fibre", "Routage IP", "Maintenance BTS", "Optique"]

NOMS = [
    "BENALI","ELHASSANI","OUALI","CHRAIBI","BENKIRANE",
    "TAZI","BERRADA","FILALI","BENSOUDA","LAHLOU",
    "MOUSSAOUI","ALAMI","SQALLI","BENOMAR","MRANI",
    "KETTANI","BELKADI","NACIRI","FASSI","BOUAYAD",
    "ZEMMOURI","CHERKAOUI","REGRAGUI","BENMOUSSA","IDRISSI",
    "TAHIRI","BENSLIMANE","ELMANSOURI","DOUKKALI","JADID",
]
PRENOMS = [
    "Youssef","Fatima","Mohammed","Amina","Hamza",
    "Zineb","Anas","Nadia","Karim","Sara",
    "Mehdi","Hajar","Rachid","Salma","Bilal",
    "Houda","Ayoub","Loubna","Omar","Meriem",
    "Soufiane","Kawtar","Adil","Ghita","Ilyas",
    "Rim","Tariq","Kenza","Reda","Yasmine",
]

CAUSES_INCOMPLETE = [
    "Pièce de rechange manquante",
    "Accès site sécurisé : autorisation requise",
    "Problème sous-jacent plus grave que prévu",
    "Conditions météorologiques défavorables",
    "Équipement nécessite remplacement complet",
    "Compétence spécialisée requise : escalade niveau 2",
    "Client absent : accès site impossible",
    "Câblage non conforme : travaux préalables requis",
    "Coupure d'alimentation : attente ONEE",
    "Route bloquée : site inaccessible",
]

CAUSES_INDISPO = [
    "Congé annuel",
    "Formation interne",
    "Arrêt maladie",
    "Mission hors secteur",
    "Réunion équipe technique",
]

# ═══════════════════════════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════════════════════════

def coord_autour(lat, lng, rayon_km=25):
    dlat = random.uniform(-rayon_km / 111, rayon_km / 111)
    dlng = random.uniform(-rayon_km / 111, rayon_km / 111)
    return round(lat + dlat, 6), round(lng + dlng, 6)

def exporter_csv(data, nom_fichier, dossier="data"):
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom_fichier)
    if not data:
        return
    with open(chemin, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=data[0].keys())
        writer.writeheader()
        for row in data:
            row_clean = {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                for k, v in row.items()
            }
            writer.writerow(row_clean)
    print(f"  CSV  : {chemin} ({len(data)} lignes)")

def exporter_json(data, nom_fichier, dossier="data"):
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom_fichier)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  JSON : {chemin} ({len(data)} entrées)")


# ═══════════════════════════════════════════════════════════════
# 1. SECTEURS
# ═══════════════════════════════════════════════════════════════

def generer_secteurs():
    return SECTEURS


# ═══════════════════════════════════════════════════════════════
# 2. ÉQUIPEMENTS
# ═══════════════════════════════════════════════════════════════

def generer_equipements(n=120):
    equips = []
    for i in range(1, n + 1):
        secteur = random.choice(SECTEURS)
        type_eq, libelle, modeles = random.choice(TYPES_EQUIPEMENT)
        lat, lng = coord_autour(secteur["lat"], secteur["lng"])
        date_inst = (TODAY - timedelta(days=random.randint(180, 5*365))).date()
        equips.append({
            "id":           i,
            "code":         f"EQ-{secteur['id']:02d}-{i:04d}",
            "nom":          f"{type_eq}_{secteur['nom'].replace(' ','_').upper()}_{i:03d}",
            "type":         type_eq,
            "libelle_type": libelle,
            "marque":       "Huawei",
            "modele":       random.choice(modeles),
            "secteur_id":   secteur["id"],
            "secteur_nom":  secteur["nom"],
            "region":       secteur["region"],
            "latitude":     lat,
            "longitude":    lng,
            "date_install": str(date_inst),
            "statut":       random.choices(
                ["ACTIF", "MAINTENANCE", "HORS_SERVICE"],
                weights=[75, 18, 7]
            )[0],
        })
    return equips


# ═══════════════════════════════════════════════════════════════
# 3. TECHNICIENS
# Tous DISPONIBLE au départ — ticket_actif_id = NULL
# ═══════════════════════════════════════════════════════════════

def generer_techniciens(n=30):
    techs = []
    for i in range(1, n + 1):
        secteur = random.choice(SECTEURS)
        lat, lng = coord_autour(secteur["lat"], secteur["lng"], rayon_km=15)
        nb_comp  = random.randint(2, 4)

        # ~15% indisponibles (congé/maladie), reste DISPONIBLE
        statut = random.choices(
            ["DISPONIBLE", "INDISPONIBLE"],
            weights=[85, 15]
        )[0]

        techs.append({
            "id":                     i,
            "matricule":              f"TECH-{i:03d}",
            "nom":                    NOMS[i - 1],
            "prenom":                 PRENOMS[i - 1],
            "telephone":              f"+212 6{random.randint(10,99)} {random.randint(100,999)} {random.randint(100,999)}",
            "secteur_id":             secteur["id"],
            "secteur_nom":            secteur["nom"],
            "latitude":               lat,
            "longitude":              lng,
            "competences":            random.sample(COMPETENCES, nb_comp),
            "statut":                 statut,
            "annees_exp":             random.randint(1, 15),
            "note_perf":              round(random.uniform(3.0, 5.0), 1),
            # Horaires fixes — alignés sur la logique dispatch / Gantt (app.py : 09h-18h)
            "heure_debut":            "09:00",
            "heure_fin":              "18:00",
            # Capacité : 1 ticket actif à la fois
            "capacite_tickets":       1,
            # NULL au départ — mis à jour lors du dispatch
            "ticket_actif_id":        None,
            "raison_indisponibilite": random.choice(CAUSES_INDISPO) if statut == "INDISPONIBLE" else None,
        })
    return techs



# ═══════════════════════════════════════════════════════════════
# 5. INCIDENTS
# Tous générés avec statut OUVERT — détectés dans les 7 derniers jours
# Aucun technicien assigné au départ
# Durée estimée calculée selon le type
# ═══════════════════════════════════════════════════════════════

def generer_incidents(equipements, n=300):
    incidents = []
    # Détections dans les 7 derniers jours (incidents récents, tous ouverts)
    debut = TODAY - timedelta(days=7)

    for i in range(1, n + 1):
        equip    = random.choice(equipements)
        type_inc = random.choice(TYPES_INCIDENT)
        code_type, libelle, severite, dur_min_ref, dur_max_ref = type_inc

        # Heure de détection aléatoire dans les 7 jours
        delta_sec = random.randint(0, int(timedelta(days=7).total_seconds()))
        date_det  = debut + timedelta(seconds=delta_sec)

        # Durée estimée de résolution (en minutes) selon le type
        duree_estimee = random.randint(dur_min_ref, dur_max_ref)

        incidents.append({
            "id":                    i,
            "code_incident":         f"INC-{date_det.strftime('%Y%m')}-{i:04d}",
            "equipement_id":         equip["id"],
            "equipement_code":       equip["code"],
            "secteur_id":            equip["secteur_id"],
            "secteur_nom":           equip["secteur_nom"],
            "region":                equip["region"],
            "latitude":              equip["latitude"],
            "longitude":             equip["longitude"],
            "type_incident":         code_type,
            "libelle":               libelle,
            "severite":              severite,
            # TOUS OUVERTS — le dispatch les passe EN_COURS
            "statut":                "OUVERT",
            "date_detection":        date_det.isoformat(),
            "date_resolution":       None,
            "duree_resolution_min":  None,
            # Durée estimée selon le type d'incident — affichée dans le dispatch
            "duree_estimee_min":     duree_estimee,
            "description":           f"{libelle} sur {equip['nom']} — secteur {equip['secteur_nom']}",
            "priorite":              {"CRITIQUE": 1, "MAJEUR": 2, "MINEUR": 3}[severite],
            # Rempli lors du dispatch
            "ticket_id":             None,
            "technicien_assigne_id": None,
            "technicien_assigne_nom":None,
        })
    return incidents


# ═══════════════════════════════════════════════════════════════
# 6. SCHEMA SQL v4
# Table ticket_historique pour l'audit trail
# Colonnes duree_estimee_min, code_incident dans fact_incidents
# ═══════════════════════════════════════════════════════════════

def generer_sql_schema():
    return """\
-- ================================================================
-- SCHEMA DATA WAREHOUSE v3 — BI TELECOM HUAWEI
-- PFE 2026 — Plateforme Supervision Réseau
-- ================================================================

-- ── DIMENSIONS ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dim_secteur (
    id        SERIAL PRIMARY KEY,
    nom       VARCHAR(100) NOT NULL,
    region    VARCHAR(100),
    latitude  DECIMAL(9,6),
    longitude DECIMAL(9,6)
);

CREATE TABLE IF NOT EXISTS dim_equipement (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(50)  UNIQUE NOT NULL,
    nom          VARCHAR(150),
    type         VARCHAR(30),
    libelle_type VARCHAR(100),
    marque       VARCHAR(50),
    modele       VARCHAR(50),
    secteur_id   INT REFERENCES dim_secteur(id),
    latitude     DECIMAL(9,6),
    longitude    DECIMAL(9,6),
    date_install DATE,
    statut       VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS dim_technicien (
    id                      SERIAL PRIMARY KEY,
    matricule               VARCHAR(20) UNIQUE NOT NULL,
    nom                     VARCHAR(100),
    prenom                  VARCHAR(100),
    telephone               VARCHAR(20),
    secteur_id              INT REFERENCES dim_secteur(id),
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    competences             TEXT,
    statut                  VARCHAR(20)  DEFAULT 'DISPONIBLE',
    annees_exp              INT,
    note_perf               DECIMAL(3,1),
    -- Horaires journaliers fixes (09h-18h, cf. DAY_START/DAY_END dans app.py)
    heure_debut             VARCHAR(5)   DEFAULT '09:00',
    heure_fin               VARCHAR(5)   DEFAULT '18:00',
    -- Contrainte : 1 ticket actif max
    capacite_tickets        INT          DEFAULT 1,
    ticket_actif_id         INT,         -- FK mis à jour lors du dispatch
    raison_indisponibilite  VARCHAR(200)
);

CREATE TABLE IF NOT EXISTS dim_temps (
    id          SERIAL PRIMARY KEY,
    date_jour   DATE UNIQUE NOT NULL,
    annee       INT,
    mois        INT,
    semaine     INT,
    jour_sem    INT,
    trimestre   INT,
    est_weekend BOOLEAN
);

-- ── TABLE DE FAITS ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fact_incidents (
    id                      SERIAL PRIMARY KEY,
    code_incident           VARCHAR(30)  UNIQUE NOT NULL,
    equipement_id           INT REFERENCES dim_equipement(id),
    secteur_id              INT REFERENCES dim_secteur(id),
    type_incident           VARCHAR(50),
    libelle                 VARCHAR(200),
    severite                VARCHAR(20),
    -- OUVERT → EN_COURS (après dispatch) → RESOLU / FERME
    statut                  VARCHAR(20)  DEFAULT 'OUVERT',
    date_detection          TIMESTAMP,
    date_resolution         TIMESTAMP,
    duree_resolution_min    INT,          -- rempli à la clôture
    duree_estimee_min       INT,          -- estimée selon type incident, affichée au dispatch
    latitude                DECIMAL(9,6),
    longitude               DECIMAL(9,6),
    description             TEXT,
    priorite                INT,          -- 1=CRITIQUE, 2=MAJEUR, 3=MINEUR
    -- Rempli lors du dispatch
    ticket_id               INT,
    technicien_assigne_id   INT REFERENCES dim_technicien(id),
    technicien_assigne_nom  VARCHAR(200)
);

CREATE TABLE IF NOT EXISTS fact_tickets (
    id                      SERIAL PRIMARY KEY,
    code_ticket             VARCHAR(20)  UNIQUE NOT NULL,
    incident_id             INT REFERENCES fact_incidents(id),
    technicien_id           INT REFERENCES dim_technicien(id),
    secteur_id              INT REFERENCES dim_secteur(id),
    severite                VARCHAR(20),
    -- Statut administratif global
    statut                  VARCHAR(20)  DEFAULT 'ASSIGNE',
    date_creation           TIMESTAMP    DEFAULT NOW(),
    date_assignation        TIMESTAMP    DEFAULT NOW(),
    date_resolution         TIMESTAMP,
    duree_intervention_min  INT,
    distance_km             DECIMAL(6,2),
    note_intervention       DECIMAL(3,1),
    -- Statut terrain mis à jour par le technicien
    -- Cycle : EN_ROUTE → SUR_SITE → EN_COURS → COMPLETE | INCOMPLET
    statut_terrain          VARCHAR(30)  DEFAULT 'EN_ROUTE',
    date_arrivee_site       TIMESTAMP,
    date_debut_intervention TIMESTAMP,
    date_fin_intervention   TIMESTAMP,
    -- Pause terrain — ancien mécanisme (statut_terrain='PAUSE', via PATCH /statut)
    pause_prise             BOOLEAN      DEFAULT FALSE,
    pause_debut_terrain     VARCHAR(5),
    pause_fin_terrain       VARCHAR(5),
    -- Pause terrain avec décompte visuel (statut_terrain='EN_PAUSE')
    -- pause_debut = horodatage de début de pause (NULL hors pause)
    -- duree_pause_min = cumul des minutes de pause, ajouté à duree_estimee_min
    -- à la reprise (cf. POST /api/tickets/<id>/pause)
    pause_debut             TIMESTAMP,
    duree_pause_min         INT          DEFAULT 0,
    -- Cause obligatoire si INCOMPLET
    cause_incomplete        TEXT,
    -- Contraintes signalées par le technicien
    contraintes             TEXT,
    -- Qui a fait la dernière mise à jour
    updated_by              VARCHAR(20),
    -- Durée estimée héritée de l'incident (pour affichage Gantt)
    duree_estimee_min       INT
);

-- ── HISTORIQUE STATUTS TICKET ─────────────────────────────────────
-- Chaque changement de statut_terrain est enregistré ici
-- Permet l'affichage de l'historique avec bouton "Historique" dans le Gantt
CREATE TABLE IF NOT EXISTS ticket_historique (
    id              SERIAL PRIMARY KEY,
    ticket_id       INT          NOT NULL REFERENCES fact_tickets(id) ON DELETE CASCADE,
    code_ticket     VARCHAR(20),
    statut_avant    VARCHAR(30),
    statut_apres    VARCHAR(30)  NOT NULL,
    -- Cause obligatoire si statut_apres = 'INCOMPLET'
    cause           TEXT,
    -- Commentaire libre admin ou technicien
    commentaire     TEXT,
    updated_by      VARCHAR(20),  -- 'ADMIN' | 'TECHNICIEN'
    updated_at      TIMESTAMP    DEFAULT NOW()
);

-- ── VUES KPI ────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_kpi_global AS
SELECT
    COUNT(*)                                                         AS total_incidents,
    COUNT(*) FILTER (WHERE statut = 'OUVERT')                        AS incidents_ouverts,
    COUNT(*) FILTER (WHERE statut = 'EN_COURS')                      AS incidents_en_cours,
    COUNT(*) FILTER (WHERE severite = 'CRITIQUE')                    AS incidents_critiques,
    ROUND(AVG(duree_resolution_min))                                 AS mttr_moyen_min,
    ROUND(AVG(duree_resolution_min) / 60.0, 1)                      AS mttr_moyen_h,
    ROUND(100.0 * COUNT(*) FILTER (WHERE statut IN ('RESOLU','FERME'))
          / NULLIF(COUNT(*), 0), 1)                                  AS taux_resolution_pct
FROM fact_incidents;

CREATE OR REPLACE VIEW v_incidents_par_secteur AS
SELECT s.nom AS secteur, s.region,
    COUNT(*)                                        AS nb_incidents,
    COUNT(*) FILTER (WHERE f.statut = 'OUVERT')     AS nb_ouverts,
    COUNT(*) FILTER (WHERE f.severite = 'CRITIQUE') AS nb_critiques,
    ROUND(AVG(f.duree_resolution_min))              AS mttr_moyen
FROM fact_incidents f
JOIN dim_secteur s ON f.secteur_id = s.id
GROUP BY s.id, s.nom, s.region
ORDER BY nb_incidents DESC;

CREATE OR REPLACE VIEW v_performance_techniciens AS
SELECT
    t.matricule,
    t.nom || ' ' || t.prenom                              AS technicien,
    t.secteur_id,
    t.statut,
    COUNT(tk.id)                                          AS tickets_traites,
    COUNT(tk.id) FILTER (WHERE tk.statut_terrain = 'COMPLETE')   AS tickets_completes,
    COUNT(tk.id) FILTER (WHERE tk.statut_terrain = 'INCOMPLET')  AS tickets_incomplets,
    ROUND(AVG(tk.duree_intervention_min))                 AS duree_moy_min,
    ROUND(AVG(tk.note_intervention), 1)                   AS note_moyenne,
    ROUND(AVG(tk.distance_km), 1)                         AS distance_moy_km
FROM dim_technicien t
LEFT JOIN fact_tickets tk ON t.id = tk.technicien_id
GROUP BY t.id, t.matricule, t.nom, t.prenom, t.secteur_id, t.statut
ORDER BY tickets_traites DESC;

-- Vue Gantt — tickets du JOUR EN COURS seulement
CREATE OR REPLACE VIEW v_gantt_aujourdhui AS
SELECT
    tk.id                        AS ticket_id,
    tk.code_ticket,
    tk.technicien_id,
    t.matricule,
    t.nom || ' ' || t.prenom     AS technicien_nom,
    t.secteur_id,
    s.nom                        AS secteur_nom,
    t.statut                     AS statut_technicien,
    -- Horaires shift
    t.heure_debut,
    t.heure_fin,
    -- Statut terrain actuel
    tk.statut_terrain,
    tk.severite,
    -- Timeline intervention
    tk.date_assignation,
    tk.date_arrivee_site,
    tk.date_debut_intervention,
    tk.date_fin_intervention,
    tk.pause_prise,
    tk.pause_debut_terrain,
    tk.pause_fin_terrain,
    -- Pause terrain avec décompte visuel
    tk.pause_debut               AS pause_terrain_debut,
    COALESCE(tk.duree_pause_min, 0) AS duree_pause_min,
    -- Cause si incomplet
    tk.cause_incomplete,
    -- Durées
    tk.duree_intervention_min,
    tk.duree_estimee_min,
    -- Incident lié
    i.code_incident,
    i.libelle                    AS incident_libelle,
    i.type_incident,
    i.severite                   AS incident_severite
FROM fact_tickets tk
JOIN dim_technicien t  ON tk.technicien_id = t.id
JOIN dim_secteur s     ON t.secteur_id = s.id
JOIN fact_incidents i  ON tk.incident_id = i.id
WHERE DATE(tk.date_assignation) = CURRENT_DATE
ORDER BY t.nom, tk.date_assignation;
"""


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Générateur BI Télécom Huawei — v4              ║")
    print(f"║  Date référence : {TODAY.strftime('%Y-%m-%d')}                  ║")
    print("╚══════════════════════════════════════════════════╝\n")

    print("── Génération ───────────────────────────────────────")
    secteurs    = generer_secteurs()
    equipements = generer_equipements(120)
    techniciens = generer_techniciens(30)
    incidents   = generer_incidents(equipements, 300)

    print(f"  Secteurs     : {len(secteurs)}")
    print(f"  Équipements  : {len(equipements)}")
    print(f"  Techniciens  : {len(techniciens)}")
    print(f"  Incidents    : {len(incidents)} (tous OUVERT)")

    print("\n── Export CSV ───────────────────────────────────────")
    exporter_csv(secteurs,    "secteurs.csv")
    exporter_csv(equipements, "equipements.csv")
    exporter_csv(techniciens, "techniciens.csv")
  
    exporter_csv(incidents,   "incidents.csv")

    print("\n── Export JSON ──────────────────────────────────────")
    exporter_json(secteurs,    "secteurs.json")
    exporter_json(equipements, "equipements.json")
    exporter_json(techniciens, "techniciens.json")
    exporter_json(incidents,   "incidents.json")

    print("\n── Export SQL Schéma ────────────────────────────────")
    os.makedirs("data", exist_ok=True)
    with open("data/schema_dwh.sql", "w", encoding="utf-8") as f:
        f.write(generer_sql_schema())
    print("  SQL  : data/schema_dwh.sql")

    # Stats
    critiques = sum(1 for i in incidents if i["severite"] == "CRITIQUE")
    majeurs   = sum(1 for i in incidents if i["severite"] == "MAJEUR")
    mineurs   = sum(1 for i in incidents if i["severite"] == "MINEUR")
    dispo     = sum(1 for t in techniciens if t["statut"] == "DISPONIBLE")
    indispo   = sum(1 for t in techniciens if t["statut"] == "INDISPONIBLE")

    print("\n── Résumé ───────────────────────────────────────────")
    print(f"  Incidents : {critiques} critiques / {majeurs} majeurs / {mineurs} mineurs")
    print(f"  Tous statut OUVERT — aucun ticket pré-assigné")
    print(f"  Techniciens : {dispo} disponibles / {indispo} indisponibles")
    print(f"  Tickets    : 0 (créés uniquement via dispatch admin)")
    print("\n  Données prêtes dans ./data/")
    print("─────────────────────────────────────────────────────\n")