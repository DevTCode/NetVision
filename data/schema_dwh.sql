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
