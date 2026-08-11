"""
app.py — v3 final
API Flask — Plateforme BI Télécom Huawei — PFE 2026
Sans authentification — connexion PostgreSQL directe
"""

import os
import json
import logging
from datetime import datetime, date, timezone

import psycopg2
import psycopg2.extras
from flask import Flask, jsonify, request, g
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)

# Encoder JSON qui retourne les dates en format local lisible
class LocalJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.strftime('%Y-%m-%dT%H:%M:%S')  # Format ISO sans Z (local)
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)

app.json_encoder = LocalJSONEncoder

# ── MODIFIER LE MOT DE PASSE ICI ──────────────────────────────
DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "bi_telecom",
    "user":     "postgres",
    "password": "admin",
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("API")

# ═══════════════════════════════════════════════════════════════
# CONNEXION DB
# ═══════════════════════════════════════════════════════════════

def get_db():
    if "db" not in g:
        try:
            g.db = psycopg2.connect(
                host=DB_CONFIG["host"],
                port=DB_CONFIG["port"],
                dbname=DB_CONFIG["dbname"],
                user=DB_CONFIG["user"],
                password=DB_CONFIG["password"],
                cursor_factory=psycopg2.extras.RealDictCursor,
                connect_timeout=10,
                options="-c timezone=Africa/Casablanca",
            )
        except psycopg2.OperationalError as e:
            log.error(f"PostgreSQL connexion échouée : {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db and not db.closed:
        db.close()

def query(sql, params=None):
    cur = get_db().cursor()
    cur.execute(sql, params or ())
    return [dict(r) for r in cur.fetchall()]

def query_one(sql, params=None):
    cur = get_db().cursor()
    cur.execute(sql, params or ())
    r = cur.fetchone()
    return dict(r) if r else {}

def pg_now():
    """Heure locale Africa/Casablanca selon l'horloge PostgreSQL (pas Python).
    Évite toute désynchronisation entre l'horloge du serveur Flask et celle
    du serveur PostgreSQL — critique pour la logique J+1 et le Gantt."""
    row = query_one("SELECT (NOW() AT TIME ZONE 'Africa/Casablanca') AS t")
    return row.get("t")

def execute(sql, params=None):
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params or ())
    db.commit()
    return cur.rowcount

def execute_returning(sql, params=None):
    db = get_db()
    cur = db.cursor()
    cur.execute(sql, params or ())
    row = cur.fetchone()
    db.commit()
    return dict(row) if row else {}

def ok(data, code=200):
    return jsonify(data), code

def err(msg, code=400):
    return jsonify({"error": msg}), code

def paginate(rows):
    page  = int(request.args.get("page",  1))
    limit = int(request.args.get("limit", 100))
    start = (page - 1) * limit
    return {"total": len(rows), "page": page, "limit": limit, "data": rows[start:start+limit]}

# ═══════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════

@app.route("/api/health")
def health():
    try:
        r = query_one("SELECT COUNT(*) AS nb FROM fact_incidents")
        t = query_one("SELECT COUNT(*) AS nb FROM fact_tickets")
        # Auto-créer colonne nb_tentatives si absente
    
        return ok({
            "status":    "ok",
            "db":        "postgresql",
            "incidents": r.get("nb", 0),
            "tickets":   t.get("nb", 0),
            "timestamp": datetime.now().isoformat(),
            "today":     date.today().isoformat(),
        })
    except Exception as e:
        return ok({"status": "error", "message": str(e)})

# ═══════════════════════════════════════════════════════════════
# KPI GLOBAL
# ═══════════════════════════════════════════════════════════════

@app.route("/api/kpi")
def kpi():
    try:
        _fermer_retards_interne()  # rattrapage auto, peu importe l'heure d'accès
        inc = query_one("""
            SELECT
                COUNT(*)                                                     AS total_incidents,
                -- Ouverts = OUVERT seulement (baisse dès assignation)
                COUNT(*) FILTER (WHERE statut = 'OUVERT')                     AS incidents_ouverts,
                COUNT(*) FILTER (WHERE statut = 'EN_COURS')                   AS incidents_en_cours,
                -- Critiques = CRITIQUE non résolu (OUVERT ou EN_COURS) → baisse au COMPLETE
                COUNT(*) FILTER (WHERE severite = 'CRITIQUE'
                    AND statut IN ('OUVERT','EN_COURS'))                       AS incidents_critiques,
                COUNT(*) FILTER (WHERE severite = 'MAJEUR'
                    AND statut IN ('OUVERT','EN_COURS'))                       AS incidents_majeurs,
                COUNT(*) FILTER (WHERE severite = 'MINEUR'
                    AND statut IN ('OUVERT','EN_COURS'))                       AS incidents_mineurs,
                COUNT(*) FILTER (WHERE statut IN ('RESOLU','FERME'))          AS incidents_resolus,
                ROUND(100.0 * COUNT(*) FILTER (WHERE statut IN ('RESOLU','FERME'))
                      / NULLIF(COUNT(*), 0), 1)                              AS taux_resolution_pct,
                ROUND(AVG(duree_resolution_min))                             AS mttr_moyen_min,
                ROUND(AVG(duree_resolution_min) / 60.0, 1)                  AS mttr_moyen_h,
                ROUND(AVG(duree_estimee_min))                                AS duree_estimee_moy_min,
                (SELECT ROUND(AVG(tk2.duree_intervention_min))
                 FROM fact_tickets tk2
                 WHERE tk2.statut_terrain='COMPLETE'
                   AND tk2.duree_intervention_min IS NOT NULL
                   AND tk2.duree_intervention_min > 0)             AS mttr_technique_min
            FROM fact_incidents
        """)

        # en_maintenance_active = tickets terrain actifs (pas COMPLETE ni INCOMPLET)
        maint = query_one("""
            SELECT COUNT(*) AS en_maintenance_active
            FROM fact_tickets
            WHERE statut_terrain IN ('EN_ROUTE','SUR_SITE','EN_COURS')
        """)

        # fact_tickets peut être vide au début — pas d'erreur
        tk = query_one("""
            SELECT
                COUNT(*) FILTER (WHERE tk.statut_terrain = 'COMPLETE')           AS tickets_complets,
                COUNT(*) FILTER (WHERE tk.statut_terrain = 'INCOMPLET'
                                   AND fi.statut = 'INCOMPLET')                  AS tickets_incomplets,
                COUNT(*) FILTER (WHERE tk.statut_terrain IN ('EN_ROUTE','SUR_SITE','EN_COURS')) AS tickets_actifs,
                COUNT(*) FILTER (WHERE (tk.statut_terrain IS NULL OR tk.statut = 'ASSIGNE')
                                   AND tk.date_assignation > (NOW() AT TIME ZONE 'Africa/Casablanca')) AS tickets_planifies,
                ROUND(AVG(tk.note_intervention), 1)                              AS note_intervention_moy,
                ROUND(AVG(tk.distance_km), 1)                                    AS distance_moy_km
            FROM fact_tickets tk
            LEFT JOIN fact_incidents fi ON tk.incident_id = fi.id
        """)

        techs = query_one("""
            SELECT
                COUNT(*) FILTER (WHERE statut = 'DISPONIBLE')      AS disponibles,
                COUNT(*) FILTER (WHERE statut = 'EN_INTERVENTION')  AS en_intervention,
                COUNT(*) FILTER (WHERE statut = 'INDISPONIBLE')     AS indisponibles,
                COUNT(*)                                            AS total
            FROM dim_technicien
        """)

        return ok({**(inc or {}), **(tk or {}), **(techs or {}), **(maint or {})})

    except Exception as e:
        log.error(f"KPI error: {e}")
        return err(str(e), 500)

# ═══════════════════════════════════════════════════════════════
# SECTEURS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/secteurs")
def secteurs():
    return ok(query("SELECT * FROM dim_secteur ORDER BY id"))

@app.route("/api/secteurs/incidents")
def secteurs_incidents():
    return ok(query("""
        SELECT s.id, s.nom, s.region, s.latitude, s.longitude,
            COUNT(i.id)                                        AS nb_incidents,
            COUNT(i.id) FILTER (WHERE i.statut = 'OUVERT')    AS nb_ouverts,
            COUNT(i.id) FILTER (WHERE i.severite = 'CRITIQUE') AS nb_critiques,
            ROUND(AVG(i.duree_resolution_min))                 AS mttr_moyen
        FROM dim_secteur s
        LEFT JOIN fact_incidents i ON i.secteur_id = s.id
        GROUP BY s.id, s.nom, s.region, s.latitude, s.longitude
        ORDER BY nb_incidents DESC NULLS LAST
    """))

# ═══════════════════════════════════════════════════════════════
# EQUIPEMENTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/equipements")
def equipements():
    filters, params = [], []
    for col in ["secteur_id", "type", "statut"]:
        val = request.args.get(col)
        if val:
            filters.append(f"{col} = %s")
            params.append(int(val) if col == "secteur_id" else val.upper())
    where = "WHERE " + " AND ".join(filters) if filters else ""
    return ok(paginate(query(f"SELECT * FROM dim_equipement {where} ORDER BY id", params)))

@app.route("/api/equipements/stats")
def equipements_stats():
    return ok(query("""
        SELECT type,
            COUNT(*)                                     AS total,
            COUNT(*) FILTER (WHERE statut='ACTIF')       AS actif,
            COUNT(*) FILTER (WHERE statut='MAINTENANCE') AS maintenance,
            COUNT(*) FILTER (WHERE statut='HORS_SERVICE')AS hors_service
        FROM dim_equipement GROUP BY type ORDER BY total DESC
    """))

# ═══════════════════════════════════════════════════════════════
# INCIDENTS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/incidents")
def incidents():
    filters, params = [], []
    statut_filtre = request.args.get("statut", "OUVERT").upper()
    if statut_filtre != "TOUS":
        filters.append("i.statut = %s")
        params.append(statut_filtre)
    for col, cast in [("severite", str), ("secteur_id", int), ("type_incident", str)]:
        val = request.args.get(col)
        if val:
            filters.append(f"i.{col} = %s")
            params.append(cast(val) if cast == int else val.upper())
    where = "WHERE " + " AND ".join(filters) if filters else ""
    rows = query(f"""
        SELECT i.*, e.type AS equipement_type, e.modele,
               s.nom AS secteur_nom_complet, s.region
        FROM fact_incidents i
        LEFT JOIN dim_equipement e ON i.equipement_id = e.id
        LEFT JOIN dim_secteur    s ON i.secteur_id    = s.id
        {where}
        ORDER BY i.priorite, i.date_detection DESC
    """, params)
    return ok(paginate(rows))

@app.route("/api/incidents/critiques")
def incidents_critiques():
    return ok(query("""
        SELECT i.*, e.type AS equipement_type, s.nom AS secteur_nom_complet
        FROM fact_incidents i
        LEFT JOIN dim_equipement e ON i.equipement_id = e.id
        LEFT JOIN dim_secteur    s ON i.secteur_id    = s.id
        WHERE i.severite = 'CRITIQUE' AND i.statut IN ('OUVERT','EN_COURS')
        ORDER BY i.priorite, i.date_detection
    """))

@app.route("/api/incidents/par-secteur")
def incidents_par_secteur():
    return ok(query("""
        SELECT s.id AS secteur_id, s.nom AS secteur, s.region,
               s.latitude, s.longitude,
               COUNT(i.id)                                          AS nb_incidents,
               COUNT(i.id) FILTER (WHERE i.statut='OUVERT')         AS nb_ouverts,
               COUNT(i.id) FILTER (WHERE i.severite='CRITIQUE')     AS nb_critiques,
               ROUND(AVG(i.duree_estimee_min))                      AS duree_estimee_moy,
               ROUND(AVG(tk.duree_intervention_min)
                   FILTER (WHERE tk.statut_terrain='COMPLETE'
                           AND tk.duree_intervention_min > 0))      AS mttr_moyen,
               ROUND(AVG(i.duree_resolution_min)
                   FILTER (WHERE i.statut IN ('RESOLU','FERME')
                           AND i.duree_resolution_min IS NOT NULL))  AS mttr_resolution
        FROM dim_secteur s
        LEFT JOIN fact_incidents i ON i.secteur_id = s.id
        LEFT JOIN fact_tickets tk  ON tk.incident_id = i.id
        GROUP BY s.id, s.nom, s.region, s.latitude, s.longitude
        ORDER BY nb_incidents DESC NULLS LAST
    """))

@app.route("/api/incidents/par-reseau")
def incidents_par_reseau():
    return ok(query("""
        SELECT e.type AS type_reseau,
               COUNT(i.id)                                       AS nb_incidents,
               COUNT(i.id) FILTER (WHERE i.severite='CRITIQUE')  AS nb_critiques,
               COUNT(i.id) FILTER (WHERE i.statut='OUVERT')      AS nb_ouverts
        FROM fact_incidents i
        JOIN dim_equipement e ON i.equipement_id = e.id
        GROUP BY e.type ORDER BY nb_incidents DESC
    """))

@app.route("/api/incidents/par-heure")
def incidents_par_heure():
    return ok(query("""
        SELECT EXTRACT(HOUR FROM date_detection)::INT AS heure,
               COUNT(*) AS nb_incidents,
               COUNT(*) FILTER (WHERE severite='CRITIQUE') AS nb_critiques
        FROM fact_incidents
        GROUP BY heure ORDER BY heure
    """))

@app.route("/api/incidents/<int:inc_id>")
def incident_detail(inc_id):
    inc = query_one("""
        SELECT i.*, e.type AS eq_type, e.modele, e.nom AS eq_nom,
               s.nom AS secteur_nom_complet, s.region
        FROM fact_incidents i
        LEFT JOIN dim_equipement e ON i.equipement_id = e.id
        LEFT JOIN dim_secteur    s ON i.secteur_id    = s.id
        WHERE i.id = %s
    """, (inc_id,))
    if not inc:
        return err("Incident non trouvé", 404)
    inc["ticket"] = query_one("""
        SELECT tk.*, t.nom, t.prenom, t.matricule
        FROM fact_tickets tk
        JOIN dim_technicien t ON tk.technicien_id = t.id
        WHERE tk.incident_id = %s
        ORDER BY tk.date_creation DESC LIMIT 1
    """, (inc_id,))
    return ok(inc)

# ═══════════════════════════════════════════════════════════════
# TECHNICIENS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/techniciens")
def techniciens():
    _fermer_retards_interne()  # rattrapage auto, peu importe l'heure d'accès
    secteur_id_filter = request.args.get("secteur_id")
    statut_filter = (request.args.get("statut") or "").upper().strip()
    sect_where = "AND t.secteur_id = %s" if secteur_id_filter else ""
    sect_params = [int(secteur_id_filter)] if secteur_id_filter else []

    rows = query(f"""
        SELECT
            t.id, t.matricule, t.nom, t.prenom, t.telephone,
            t.secteur_id, t.latitude, t.longitude, t.competences,
            t.annees_exp, t.note_perf, t.heure_debut, t.heure_fin,
            t.raison_indisponibilite, t.ticket_actif_id,
            s.nom AS secteur_nom,
            CASE
              WHEN tk.id IS NOT NULL AND tk.statut_terrain = 'EN_PAUSE' THEN 'EN_PAUSE'
              WHEN tk.id IS NOT NULL THEN 'EN_INTERVENTION'
              ELSE t.statut
            END AS statut,
            tk.id           AS ticket_actif_id_calc,
            tk.code_ticket  AS ticket_actif_code,
            tk.statut_terrain AS ticket_actif_statut
        FROM dim_technicien t
        LEFT JOIN dim_secteur s ON t.secteur_id = s.id
        LEFT JOIN LATERAL (
            SELECT id, code_ticket, statut_terrain
            FROM fact_tickets
            WHERE technicien_id = t.id
              AND (statut_terrain IS NULL
                   OR statut_terrain NOT IN ('COMPLETE','INCOMPLET'))
              AND date_assignation <= (NOW() AT TIME ZONE 'Africa/Casablanca')
            ORDER BY date_creation DESC
            LIMIT 1
        ) tk ON TRUE
        WHERE 1=1 {sect_where}
        ORDER BY t.nom
    """, sect_params)

    if statut_filter:
        rows = [r for r in rows if (r.get('statut') or '').upper() == statut_filter]

    return ok(paginate(rows))

@app.route("/api/techniciens/disponibles")
def techniciens_disponibles():
    sid = request.args.get("secteur_id")
    sql = """SELECT * FROM dim_technicien
             WHERE statut = 'DISPONIBLE' AND ticket_actif_id IS NULL"""
    params = []
    if sid:
        sql += " AND secteur_id = %s"
        params.append(int(sid))
    sql += " ORDER BY note_perf DESC"
    return ok(query(sql, params))

@app.route("/api/techniciens/<int:tech_id>")
def technicien_detail(tech_id):
    tech = query_one("SELECT * FROM dim_technicien WHERE id = %s", (tech_id,))
    if not tech:
        return err("Technicien non trouvé", 404)
    tech["tickets_recents"] = query("""
        SELECT tk.code_ticket, tk.severite, tk.statut, tk.statut_terrain,
               tk.date_creation, tk.duree_intervention_min, tk.note_intervention,
               i.libelle AS incident_libelle
        FROM fact_tickets tk
        JOIN fact_incidents i ON tk.incident_id = i.id
        WHERE tk.technicien_id = %s
        ORDER BY tk.date_creation DESC LIMIT 10
    """, (tech_id,))
    tech["stats"] = query_one("""
        SELECT
            COUNT(*)                                              AS total_tickets,
            COUNT(*) FILTER (WHERE statut_terrain='COMPLETE')    AS tickets_completes,
            COUNT(*) FILTER (WHERE statut_terrain='INCOMPLET')   AS tickets_incomplets,
            ROUND(AVG(duree_intervention_min))                   AS duree_moy_min,
            ROUND(AVG(note_intervention), 1)                     AS note_moyenne
        FROM fact_tickets WHERE technicien_id = %s
    """, (tech_id,))
    # Ticket actif courant — dériver le vrai statut
    # IMPORTANT : exclure les tickets J+1 dont la date_assignation est future,
    # sinon un technicien avec un ticket planifié pour demain serait considéré
    # à tort comme EN_INTERVENTION aujourd'hui.
    tk_actif = query_one("""
        SELECT id, code_ticket, statut_terrain, date_assignation,
               COALESCE(duree_pause_min, 0) AS duree_pause_min
        FROM fact_tickets
        WHERE technicien_id = %s
          AND (statut_terrain IS NULL
               OR statut_terrain NOT IN ('COMPLETE','INCOMPLET'))
          AND date_assignation <= (NOW() AT TIME ZONE 'Africa/Casablanca')
        ORDER BY date_creation DESC LIMIT 1
    """, (tech_id,))

    if tk_actif and tk_actif.get("id"):
        # Technicien EN_INTERVENTION — synchroniser dim_technicien si désynchronisé
        real_statut = 'EN_PAUSE' if tk_actif.get("statut_terrain") == 'EN_PAUSE' else 'EN_INTERVENTION'
        if tech.get("statut") != real_statut:
            try:
                execute("UPDATE dim_technicien SET statut=%s, ticket_actif_id=%s WHERE id=%s",
                        (real_statut, tk_actif["id"], tech_id))
                tech["statut"] = real_statut
                log.info(f"Technicien {tech_id} resync → {real_statut}")
            except Exception as _se:
                log.warning(f"Resync statut failed: {_se}")
                tech["statut"] = real_statut
        tech["ticket_actif_statut"] = tk_actif.get("statut_terrain")
        tech["ticket_actif_code"]   = tk_actif.get("code_ticket")
        tech["ticket_actif_id"]     = tk_actif.get("id")
    else:
        # Aucun ticket actif — technicien DISPONIBLE si DB dit autrement
        if tech.get("statut") == "EN_INTERVENTION":
            try:
                execute("UPDATE dim_technicien SET statut='DISPONIBLE', ticket_actif_id=NULL WHERE id=%s",
                        (tech_id,))
                tech["statut"] = "DISPONIBLE"
                log.info(f"Technicien {tech_id} resync → DISPONIBLE (aucun ticket actif)")
            except Exception:
                tech["statut"] = "DISPONIBLE"
        tech["ticket_actif_statut"] = None
        tech["ticket_actif_code"]   = None
        tech["ticket_actif_id"]     = None

    return ok(tech)

# ═══════════════════════════════════════════════════════════════
# CARTE — incidents OUVERT uniquement
# ═══════════════════════════════════════════════════════════════

@app.route("/api/carte")
def carte():
    _fermer_retards_interne()  # rattrapage auto, peu importe l'heure d'accès
    sects = query("SELECT * FROM dim_secteur")
    equips = query("""
        SELECT id, code, type, statut, secteur_id, latitude, longitude, nom, modele
        FROM dim_equipement WHERE latitude IS NOT NULL
    """)
    incs = query("""
        SELECT i.id, i.code_incident, i.severite, i.statut,
               i.latitude, i.longitude, i.secteur_id,
               i.type_incident, i.priorite, i.duree_estimee_min,
               i.technicien_assigne_nom,
               s.nom AS secteur_nom
        FROM fact_incidents i
        JOIN dim_secteur s ON i.secteur_id = s.id
        WHERE i.statut IN ('OUVERT','EN_COURS','INCOMPLET') AND i.latitude IS NOT NULL
        ORDER BY i.priorite, i.date_detection DESC
    """)
    techs = query("""
        SELECT t.id, t.matricule, t.nom, t.prenom,
               CASE WHEN tk.id IS NOT NULL THEN 'EN_INTERVENTION'
                    ELSE t.statut END AS statut,
               t.secteur_id, t.latitude, t.longitude,
               t.ticket_actif_id, t.note_perf,
               t.heure_debut, t.heure_fin,
               tk.id AS tk_actif_id,
               tk.code_ticket AS tk_actif_code,
               tk.statut_terrain AS tk_actif_statut
        FROM dim_technicien t
        LEFT JOIN LATERAL (
            SELECT id, code_ticket, statut_terrain
            FROM fact_tickets
            WHERE technicien_id = t.id
              AND (statut_terrain IS NULL
                   OR statut_terrain NOT IN ('COMPLETE','INCOMPLET'))
              AND date_assignation <= (NOW() AT TIME ZONE 'Africa/Casablanca')
            ORDER BY date_assignation ASC LIMIT 1
        ) tk ON TRUE
        WHERE t.latitude IS NOT NULL
    """)
    return ok({
        "secteurs":    sects,
        "equipements": equips,
        "incidents":   incs,
        "techniciens": techs,
        "timestamp":   datetime.now().isoformat(),
        "today":       date.today().isoformat(),
    })

# ═══════════════════════════════════════════════════════════════
# DISPATCH
# ═══════════════════════════════════════════════════════════════

@app.route("/api/dispatch/<int:inc_id>")
def dispatch(inc_id):
    inc = query_one("""
        SELECT i.*, s.nom AS secteur_nom
        FROM fact_incidents i
        JOIN dim_secteur s ON i.secteur_id = s.id
        WHERE i.id = %s AND i.statut = 'OUVERT'
    """, (inc_id,))
    if not inc:
        return err("Incident non trouvé ou déjà pris en charge", 404)

    techs = query("""
        SELECT *,
            ROUND(111.0 * SQRT(
                POWER(latitude  - %s, 2) +
                POWER(longitude - %s, 2)
            )::NUMERIC, 1) AS distance_km,
            CASE WHEN secteur_id = %s THEN TRUE ELSE FALSE END AS meme_secteur
        FROM dim_technicien
        WHERE statut = 'DISPONIBLE' AND ticket_actif_id IS NULL
        ORDER BY secteur_id = %s DESC, distance_km ASC
    """, (inc["latitude"], inc["longitude"], inc["secteur_id"], inc["secteur_id"]))

    return ok({
        "incident":        inc,
        "techniciens":     techs,
        "meme_secteur":    [t for t in techs if t["meme_secteur"]],
        "autres_secteurs": [t for t in techs if not t["meme_secteur"]][:5],
    })

@app.route("/api/dispatch/assigner", methods=["POST"])
def assigner_ticket():
    data    = request.get_json()
    inc_id  = data.get("incident_id")
    tech_id = data.get("technicien_id")
    if not inc_id or not tech_id:
        return err("incident_id et technicien_id requis")

    inc  = query_one("SELECT * FROM fact_incidents WHERE id = %s", (inc_id,))
    tech = query_one("SELECT * FROM dim_technicien  WHERE id = %s", (tech_id,))

    if not inc:  return err("Incident non trouvé", 404)
    if inc.get("statut") != "OUVERT": return err("Incident déjà pris en charge")
    if not tech: return err("Technicien non trouvé", 404)
    if tech.get("statut") != "DISPONIBLE" or tech.get("ticket_actif_id"):
        return err(f"Technicien non disponible (statut={tech.get('statut')})")

    dist = round(111.0 * ((
        (float(tech["latitude"])  - float(inc["latitude"]))**2 +
        (float(tech["longitude"]) - float(inc["longitude"]))**2
    )**0.5), 1)

    now = pg_now()  # horloge PostgreSQL, pas Python
    duree_min = inc.get("duree_estimee_min") or 60

    # ── Vision : duree_estimee = tout inclus (trajet + intervention) ──
    # J+1 si T0 + duree dépasse 18h (pas de pause déjeuner fixe)
    from datetime import timedelta as _td
    DAY_START = 9 * 60   # 09h00
    DAY_END   = 18 * 60  # 18h00

    heure_now_min = now.hour * 60 + now.minute
    avant_journee = heure_now_min < DAY_START
    apres_journee = heure_now_min >= DAY_END

    # Hors horaires (avant 9h ou apres 18h) → J+1 direct
    if avant_journee or apres_journee:
        reprise_demain = True
        fin_eff = DAY_START + duree_min
    else:
        fin_eff = heure_now_min + duree_min
        reprise_demain = fin_eff > DAY_END

    if reprise_demain:
        # Popup admin — ne pas insérer
        fh, fm = int(fin_eff // 60), int(fin_eff % 60)
        date_j1 = (now + _td(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return jsonify({
            "success": False, "confirmation_requise": True,
            "fin_estimee":         f"{fh:02d}h{fm:02d}",
            "date_assignation_j1": date_j1.isoformat(),
            "technicien":          f"{tech['prenom']} {tech['nom']}",
            "matricule":           tech["matricule"],
            "incident_code":       inc.get("code_incident"),
            "severite":            inc.get("severite"),
            "duree_estimee":       duree_min,
            "distance_km":         dist,
        }), 200

    date_assignation = now
    fh, fm = int(fin_eff // 60), int(fin_eff % 60)

    # Connexion dédiée indépendante pour garantir le commit
    import psycopg2 as _pg; import psycopg2.extras as _pext
    db = _pg.connect(host=DB_CONFIG["host"], port=DB_CONFIG["port"],
        dbname=DB_CONFIG["dbname"], user=DB_CONFIG["user"], password=DB_CONFIG["password"],
        cursor_factory=_pext.RealDictCursor, options="-c timezone=Africa/Casablanca")
    cur = db.cursor()

    try:
        cur.execute("""
            INSERT INTO fact_tickets
                (code_ticket, incident_id, technicien_id, secteur_id, severite,
                 statut, statut_terrain, date_creation, date_assignation,
                 distance_km, duree_estimee_min, updated_by)
            VALUES (%s,%s,%s,%s,%s,'ASSIGNE',NULL,%s,%s,%s,%s,'ADMIN')
            RETURNING id, code_ticket
        """, (
            f"TKT-{inc_id:04d}-{tech_id:03d}-{now.strftime('%H%M%S')}",
            inc_id, tech_id, inc["secteur_id"], inc["severite"],
            now, date_assignation, dist, duree_min,
        ))
        new = dict(cur.fetchone())
        new_id, new_code = new["id"], new["code_ticket"]

        # Si reprise demain : technicien reste DISPONIBLE jusqu'à demain
        if reprise_demain:
            cur.execute("UPDATE dim_technicien SET statut='DISPONIBLE', ticket_actif_id=%s WHERE id=%s",
                        (new_id, tech_id))
        else:
            cur.execute("UPDATE dim_technicien SET statut='EN_INTERVENTION', ticket_actif_id=%s WHERE id=%s",
                        (new_id, tech_id))
        cur.execute("""UPDATE fact_incidents
                       SET statut='EN_COURS', ticket_id=%s,
                           technicien_assigne_id=%s, technicien_assigne_nom=%s
                       WHERE id=%s""",
                    (new_id, tech_id, f"{tech['prenom']} {tech['nom']}", inc_id))
        commentaire = f"Reprise demain {date_assignation.strftime('%d/%m à %H:%M')}" if reprise_demain else "Ticket créé et assigné"
        cur.execute("""INSERT INTO ticket_historique
                       (ticket_id,code_ticket,statut_avant,statut_apres,updated_by,commentaire,updated_at)
                       VALUES (%s,%s,NULL,'ASSIGNE','ADMIN',%s,%s)""",
                    (new_id, new_code, commentaire, now))
        db.commit()
        db.close()
        log.info(f"Dispatch OK: {new_code} tech={tech_id}")
    except Exception as e:
        try: db.rollback(); db.close()
        except: pass
        log.error(f"Dispatch ERREUR: {e}")
        return err(f"Erreur création ticket: {str(e)}", 500)

    return jsonify({
        "success":          True,
        "ticket_id":        new_id,
        "code_ticket":      new_code,
        "technicien":       f"{tech['prenom']} {tech['nom']}",
        "matricule":        tech["matricule"],
        "distance_km":      dist,
        "duree_estimee":    duree_min,
        "fin_estimee_heure": f"{fh:02d}h{fm:02d}",
        "reprise_demain":   False,
        "date_assignation": date_assignation.isoformat(),
        "date_creation":    now.isoformat(),
    }), 201

# ═══════════════════════════════════════════════════════════════
# TICKETS
# ═══════════════════════════════════════════════════════════════

@app.route("/api/tickets")
def tickets():
    filters, params = [], []
    for col, cast in [("statut",str),("severite",str),
                      ("statut_terrain",str),("secteur_id",int),("technicien_id",int)]:
        val = request.args.get(col)
        if val:
            filters.append(f"tk.{col} = %s")
            params.append(cast(val) if cast==int else val.upper())
    if request.args.get("today"):
        filters.append("DATE(tk.date_creation) = CURRENT_DATE")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    rows = query(f"""
        SELECT tk.*,
               t.nom AS tech_nom, t.prenom AS tech_prenom, t.matricule AS tech_matricule,
               i.libelle AS incident_libelle, i.type_incident, i.code_incident,
               s.nom AS secteur_nom_complet
        FROM fact_tickets tk
        JOIN dim_technicien t  ON tk.technicien_id = t.id
        JOIN fact_incidents  i ON tk.incident_id   = i.id
        JOIN dim_secteur     s ON tk.secteur_id    = s.id
        {where}
        ORDER BY tk.date_creation DESC
    """, params)
    return ok(paginate(rows))

@app.route("/api/tickets/<int:tk_id>")
def ticket_detail(tk_id):
    tk = query_one("""
        SELECT tk.*,
               t.nom, t.prenom, t.matricule, t.telephone,
               t.heure_debut, t.heure_fin,
               i.libelle, i.type_incident, i.code_incident,
               i.description, i.latitude AS inc_lat, i.longitude AS inc_lng,
               e.nom AS eq_nom, e.type AS eq_type, e.modele,
               s.nom AS secteur_nom
        FROM fact_tickets tk
        JOIN dim_technicien t  ON tk.technicien_id = t.id
        JOIN fact_incidents  i ON tk.incident_id   = i.id
        JOIN dim_equipement  e ON i.equipement_id  = e.id
        JOIN dim_secteur     s ON tk.secteur_id    = s.id
        WHERE tk.id = %s
    """, (tk_id,))
    if not tk:
        return err("Ticket non trouvé", 404)
    tk["historique"] = query("""
        SELECT * FROM ticket_historique
        WHERE ticket_id = %s ORDER BY updated_at ASC
    """, (tk_id,))
    return ok(tk)

@app.route("/api/tickets/<int:tk_id>/historique")
def ticket_historique_route(tk_id):
    return ok(query("SELECT * FROM ticket_historique WHERE ticket_id=%s ORDER BY updated_at", (tk_id,)))

@app.route("/api/tickets/<int:tk_id>/statut", methods=["PATCH"])
def update_ticket_statut(tk_id):
    data = request.get_json()
    if not data:
        return err("Body JSON requis")

    nouveau = (data.get("statut_terrain") or "").strip().upper()
    valides = ["EN_ROUTE","SUR_SITE","EN_COURS","PAUSE","COMPLETE","INCOMPLET"]
    if nouveau not in valides:
        return err(f"statut_terrain invalide : {valides}")
    if nouveau == "INCOMPLET" and not data.get("cause_incomplete"):
        return err("cause_incomplete obligatoire si INCOMPLET")

    # Lire le ticket directement depuis DB (pas via _ser pour garder les datetime)
    cur_raw = get_db().cursor()
    cur_raw.execute("SELECT * FROM fact_tickets WHERE id = %s", (tk_id,))
    tk_raw = cur_raw.fetchone()
    if not tk_raw:
        return err("Ticket non trouvé", 404)
    tk = dict(tk_raw)  # datetime Python natifs — pas sérialisés

    ancien = tk.get("statut_terrain")
    now    = datetime.now()
    sets   = ["statut_terrain = %s", "updated_by = %s"]
    vals   = [nouveau, data.get("updated_by", "TECHNICIEN")]

    def parse_dt(v):
        """Convertit string ISO ou datetime en datetime Python."""
        if v is None: return None
        if isinstance(v, datetime): return v
        if isinstance(v, str):
            try: return datetime.fromisoformat(v)
            except: return None
        return None

    if nouveau == "SUR_SITE":
        sets.append("date_arrivee_site = %s"); vals.append(now)
    elif nouveau == "EN_COURS" and not tk.get("date_debut_intervention"):
        sets.append("date_debut_intervention = %s"); vals.append(now)
    elif nouveau in ("COMPLETE", "INCOMPLET"):
        sets.append("date_fin_intervention = %s"); vals.append(now)
        # Durée réelle = depuis date_assignation (vision tout inclus : trajet + intervention)
        da = parse_dt(tk.get("date_assignation"))
        if da:
            duree = max(1, int((now - da).total_seconds() / 60))
            sets.append("duree_intervention_min = %s"); vals.append(duree)
            log.info(f"Ticket {tk_id} COMPLETE — durée réelle (depuis assignation): {duree} min")
    elif nouveau == "PAUSE":
        sets.append("pause_prise = TRUE")
        sets.append("pause_debut_terrain = %s"); vals.append(now.strftime("%H:%M"))

    if data.get("cause_incomplete"):
        sets.append("cause_incomplete = %s"); vals.append(data["cause_incomplete"])
    if data.get("note_intervention") is not None:
        sets.append("note_intervention = %s"); vals.append(int(data["note_intervention"]))

    vals.append(tk_id)
    execute(f"UPDATE fact_tickets SET {', '.join(sets)} WHERE id = %s", vals)

    execute("""INSERT INTO ticket_historique
               (ticket_id,code_ticket,statut_avant,statut_apres,cause,commentaire,updated_by,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tk_id, tk.get("code_ticket"), ancien, nouveau,
             data.get("cause_incomplete"), data.get("commentaire"),
             data.get("updated_by","TECHNICIEN"), now))

    if nouveau in ("COMPLETE", "INCOMPLET"):
        # Durée réelle pour INCOMPLET
        if nouveau == "INCOMPLET" and not tk.get("duree_intervention_min"):
            da = parse_dt(tk.get("date_assignation"))
            if da:
                duree = max(1, int((now - da).total_seconds() / 60))
                execute("UPDATE fact_tickets SET duree_intervention_min=%s WHERE id=%s", (duree, tk_id))

        execute("UPDATE dim_technicien SET statut='DISPONIBLE', ticket_actif_id=NULL WHERE id=%s",
                (tk["technicien_id"],))

        if nouveau == "COMPLETE":
            da = parse_dt(tk.get("date_assignation"))
            duree_reelle = int((now - da).total_seconds() / 60) if da else None
            execute("""UPDATE fact_incidents
                       SET statut='RESOLU',
                           date_resolution=%s,
                           duree_resolution_min=%s,
                           technicien_assigne_id=NULL,
                           technicien_assigne_nom=NULL
                       WHERE id=%s""",
                    (now, duree_reelle, tk["incident_id"]))
            log.info(f"Incident {tk['incident_id']} RESOLU — durée réelle: {duree_reelle} min")

        elif nouveau == "INCOMPLET":
           execute("""UPDATE fact_incidents
           SET statut='INCOMPLET',
               ticket_id=NULL,
               technicien_assigne_id=NULL,
               technicien_assigne_nom=NULL
           WHERE id=%s""",
        (tk["incident_id"],))

    return ok({"success":True,"ticket_id":tk_id,"statut_avant":ancien,"statut_terrain":nouveau})

@app.route("/api/tickets/incomplets")
def tickets_incomplets():
    return ok(query("""
        SELECT tk.*, t.nom, t.prenom, t.matricule,
               i.libelle, i.type_incident, i.code_incident, s.nom AS secteur_nom
        FROM fact_tickets tk
        JOIN dim_technicien t ON tk.technicien_id = t.id
        JOIN fact_incidents  i ON tk.incident_id  = i.id
        JOIN dim_secteur     s ON tk.secteur_id   = s.id
        WHERE tk.statut_terrain = 'INCOMPLET'
        ORDER BY tk.date_creation DESC
    """))

# ═══════════════════════════════════════════════════════════════
# GANTT — AUJOURD'HUI UNIQUEMENT
# ═══════════════════════════════════════════════════════════════
@app.route("/api/gantt")
def gantt():
    _fermer_retards_interne()  # rattrapage auto, peu importe l'heure d'accès
    # IMPORTANT : utiliser l'horloge PostgreSQL (pas Python) pour déterminer
    # "aujourd'hui", car les deux peuvent être désynchronisées (ex: serveur
    # PostgreSQL et machine Flask sur des hôtes/horloges différents).
    pg_today_row = query_one("SELECT (NOW() AT TIME ZONE 'Africa/Casablanca')::DATE AS d")
    pg_today = pg_today_row.get("d")  # objet date Python

    date_param = request.args.get("date", pg_today.isoformat())
    try:
        gantt_date = date.fromisoformat(date_param)
    except:
        gantt_date = pg_today
    today = gantt_date.isoformat()
    is_today = (gantt_date == pg_today)

    sid = request.args.get("secteur_id")

    if is_today:
        # Le ticket appartient TOUJOURS au jour exact de sa date_assignation,
        # quel que soit son statut actuel ou la date de clôture (même via
        # clôture auto), pour éviter qu'un ticket J+1 d'hier resté actif
        # ne réapparaisse à tort aujourd'hui.
        join_cond = """DATE(tk.date_assignation) = %s
                AND (
                  tk.statut_terrain IN ('EN_ROUTE','SUR_SITE','EN_COURS','EN_PAUSE',
                                         'COMPLETE','INCOMPLET')
                  OR
                  ((tk.statut_terrain IS NULL OR tk.statut_terrain = 'ASSIGNE')
                   AND tk.date_assignation <= (NOW() AT TIME ZONE 'Africa/Casablanca'))
                )"""
        q_params = [today]
    else:
        # Jour futur ou passé : tickets planifiés pour ce jour (ASSIGNE/NULL,
        # typiquement les J+1 pas encore commencés) + tickets déjà terminés
        # ce jour-là (COMPLETE/INCOMPLET)
        join_cond = """(
                ((tk.statut_terrain IS NULL OR tk.statut_terrain = 'ASSIGNE')
                 AND DATE(tk.date_assignation) = %s)
                OR
                (tk.statut_terrain IN ('COMPLETE','INCOMPLET')
                 AND DATE(tk.date_assignation) = %s)
              )"""
        q_params = [today, today]

    if sid:
        q_params.append(int(sid))

    rows = query(f"""
        SELECT
            t.id AS technicien_id, t.matricule,
            t.nom || ' ' || t.prenom AS technicien_nom,
            t.secteur_id, s.nom AS secteur_nom,
            t.statut AS statut_technicien,
            t.heure_debut, t.heure_fin,
            t.raison_indisponibilite,
            t.telephone,
            tk.id AS ticket_id, tk.incident_id, tk.code_ticket, tk.severite, tk.statut_terrain,
            tk.date_assignation, tk.date_arrivee_site,
            tk.date_debut_intervention, tk.date_fin_intervention,
            tk.pause_prise, tk.pause_debut_terrain, tk.pause_fin_terrain,
            tk.cause_incomplete, tk.duree_intervention_min, tk.duree_estimee_min,
            COALESCE(tk.duree_pause_min, 0) AS duree_pause_min,
            tk.updated_by,
            i.libelle AS incident_libelle, i.type_incident, i.code_incident
        FROM dim_technicien t
        LEFT JOIN dim_secteur s ON t.secteur_id = s.id
        LEFT JOIN fact_tickets tk
            ON t.id = tk.technicien_id AND ({join_cond})
        LEFT JOIN fact_incidents i ON tk.incident_id = i.id
        WHERE tk.id IS NOT NULL
        {"AND t.secteur_id = %s" if sid else ""}
        ORDER BY t.nom, tk.date_assignation
    """, q_params)

    mp = {}
    for r in rows:
        tid = r["technicien_id"]
        if tid not in mp:
            mp[tid] = {
                "technicien_id": tid, "matricule": r["matricule"],
                "nom": r["technicien_nom"], "secteur_id": r["secteur_id"],
                "secteur_nom": r["secteur_nom"], "statut": r["statut_technicien"],
                "heure_debut": r["heure_debut"], "heure_fin": r["heure_fin"],
                "raison_indisponibilite": r["raison_indisponibilite"],
                "telephone": r.get("telephone"),
                "tickets": [],
            }
        if r["ticket_id"]:
            iso = lambda v: v if isinstance(v, str) else (v.strftime('%Y-%m-%dT%H:%M:%S') if v else None)
            mp[tid]["tickets"].append({
                "ticket_id": r["ticket_id"], "code_ticket": r["code_ticket"],
                "incident_id": r["incident_id"] if "incident_id" in r else None,
                "incident_libelle": r["incident_libelle"],
                "incident_code": r["code_incident"],
                "type_incident": r["type_incident"],
                "severite": r["severite"], "statut_terrain": r["statut_terrain"],
                "date_assignation":        iso(r["date_assignation"]),
                "date_arrivee_site":       iso(r["date_arrivee_site"]),
                "date_debut_intervention": iso(r["date_debut_intervention"]),
                "date_fin_intervention":   iso(r["date_fin_intervention"]),
                "pause_prise": r["pause_prise"],
                "cause_incomplete": r["cause_incomplete"],
                "duree_min": r["duree_intervention_min"],
                "duree_estimee_min": r["duree_estimee_min"],
                "duree_pause_min": r.get("duree_pause_min") or 0,
                "duree_totale_min": (r["duree_estimee_min"] or 0) + (r.get("duree_pause_min") or 0),
                "updated_by": r.get("updated_by"),
            })

    result = list(mp.values())
    return ok({"date": today, "techniciens": result,
               "total_tech": len(result),
               "avec_tickets": sum(1 for t in result if t["tickets"]),
               "sans_tickets": sum(1 for t in result if not t["tickets"])})

@app.route("/api/techniciens/<int:tech_id>/ticket-actif")
def ticket_actif_technicien(tech_id):
    """Retourne le ticket actif d un technicien (non terminé). Robuste aux colonnes manquantes."""
    # Query de base sans colonnes optionnelles
    tk = query_one("""
        SELECT tk.id, tk.code_ticket, tk.technicien_id, tk.incident_id,
               tk.secteur_id, tk.statut_terrain, tk.statut,
               tk.date_assignation, tk.date_arrivee_site,
               tk.date_debut_intervention, tk.date_fin_intervention,
               tk.duree_estimee_min, tk.duree_intervention_min,
               tk.cause_incomplete,
               i.libelle AS incident_libelle, i.type_incident, i.code_incident,
               i.description, i.latitude AS inc_lat, i.longitude AS inc_lng,
               i.severite AS inc_severite,
               e.nom AS eq_nom, e.type AS eq_type,
               s.nom AS secteur_nom_complet
        FROM fact_tickets tk
        LEFT JOIN fact_incidents i ON tk.incident_id = i.id
        LEFT JOIN dim_equipement e ON i.equipement_id = e.id
        LEFT JOIN dim_secteur s ON tk.secteur_id = s.id
        WHERE tk.technicien_id = %s
          AND (
            tk.statut_terrain IS NULL
            OR tk.statut_terrain NOT IN ('COMPLETE','INCOMPLET')
          )
        ORDER BY tk.date_creation DESC
        LIMIT 1
    """, (tech_id,))
    if not tk:
        return ok(None)

    # Colonnes optionnelles (peuvent ne pas exister si migration pas encore faite)
    try:
        extra = query_one(
            "SELECT COALESCE(duree_pause_min,0) AS duree_pause_min, updated_by FROM fact_tickets WHERE id=%s",
            (tk["id"],))
        if extra:
            tk["duree_pause_min"] = extra.get("duree_pause_min", 0)
            tk["updated_by"] = extra.get("updated_by")
    except Exception:
        tk["duree_pause_min"] = 0
        tk["updated_by"] = None

    try:
        tk["historique"] = query(
            "SELECT * FROM ticket_historique WHERE ticket_id = %s ORDER BY updated_at",
            (tk["id"],))
    except Exception:
        tk["historique"] = []

    return ok(tk)

def _fermer_retards_interne():
    """Ferme automatiquement les tickets dont la durée estimée est dépassée.
    Fonction interne réutilisable, appelée systématiquement en tête des
    endpoints critiques (gantt, kpi, carte, techniciens) afin que les
    retards soient TOUJOURS rattrapés au premier accès, indépendamment
    de l'heure à laquelle l'application est ouverte ou d'un quelconque
    setInterval côté navigateur."""
    now = pg_now()  # horloge PostgreSQL, pas Python

    candidats = query("""
        SELECT tk.id, tk.code_ticket, tk.technicien_id, tk.incident_id,
               tk.statut_terrain, tk.statut,
               tk.duree_estimee_min, tk.date_assignation,
               t.nom, t.prenom, t.telephone
        FROM fact_tickets tk
        JOIN dim_technicien t ON tk.technicien_id = t.id
        WHERE tk.statut_terrain IS DISTINCT FROM 'COMPLETE'
          AND tk.statut_terrain IS DISTINCT FROM 'INCOMPLET'
          AND tk.statut_terrain IS DISTINCT FROM 'EN_PAUSE'
          AND tk.duree_estimee_min IS NOT NULL
          AND tk.date_assignation IS NOT NULL
    """)

    from datetime import timedelta
    from email.utils import parsedate_to_datetime as _prfc

    def _plocal(v):
        if v is None: return None
        if isinstance(v, datetime):
            return v.replace(tzinfo=None) if v.tzinfo else v
        s = str(v)
        try: return datetime.fromisoformat(s)
        except: pass
        try: return _prfc(s).replace(tzinfo=None)
        except: return None

    retards = []
    for tk in candidats:
        da = _plocal(tk.get('date_assignation'))
        if not da: continue
        duree = int(tk.get('duree_estimee_min') or 60)
        fin = da + timedelta(minutes=duree)
        if now > fin:
            retards.append({**tk, '_da': da})

    if not retards:
        return []

    log.info(f"fermer-retards (auto): {len(retards)} retard(s) détecté(s)")

    fermes = []
    for tk in retards:
        da = tk['_da']
        duree_estimee = int(tk.get('duree_estimee_min') or 60)
        # Heure théorique de fin = quand le ticket aurait dû se terminer,
        # PAS le moment où ce code s'exécute (qui peut être bien plus tard
        # si personne n'a ouvert l'app entre temps).
        fin_theorique = da + timedelta(minutes=duree_estimee)
        duree_reelle = duree_estimee  # le ticket "dure" exactement sa durée estimée

        execute("""
            UPDATE fact_tickets
            SET statut = 'INCOMPLET',
                statut_terrain = 'INCOMPLET',
                cause_incomplete = 'Durée estimée dépassée — clôture automatique NOC',
                date_fin_intervention = %s,
                duree_intervention_min = %s,
                updated_by = 'SYSTEME'
            WHERE id = %s
        """, (fin_theorique, duree_reelle, tk["id"]))

        execute("""
            UPDATE dim_technicien
            SET statut = 'DISPONIBLE', ticket_actif_id = NULL
            WHERE id = %s
        """, (tk["technicien_id"],))

        execute("""
            UPDATE fact_incidents
            SET statut = 'OUVERT',
                ticket_id = NULL,
                technicien_assigne_id = NULL,
                technicien_assigne_nom = NULL
            WHERE id = %s
        """, (tk["incident_id"],))

        execute("""
            INSERT INTO ticket_historique
            (ticket_id,code_ticket,statut_avant,statut_apres,cause,commentaire,updated_by,updated_at)
            VALUES (%s,%s,%s,'INCOMPLET','Durée dépassée','Clôture automatique par le système','SYSTEME',%s)
        """, (tk["id"], tk["code_ticket"], tk.get("statut_terrain"), fin_theorique))

        fermes.append({
            "ticket_id":   tk["id"],
            "code_ticket": tk["code_ticket"],
            "technicien":  f"{tk['prenom']} {tk['nom']}",
            "telephone":   tk.get("telephone"),
        })

    log.info(f"fermer-retards (auto): {len(fermes)} ticket(s) fermé(s)")
    return fermes


@app.route("/api/tickets/fermer-retards", methods=["POST"])
def fermer_tickets_retards():
    """Ferme automatiquement les tickets dont la durée estimée est dépassée."""
    fermes = _fermer_retards_interne()
    return ok({"fermes": fermes, "count": len(fermes)})


@app.route("/api/dispatch/assigner-j1", methods=["POST"])
def assigner_ticket_j1():
    """Confirme l'assignation J+1 après popup admin — insère demain 09h00."""
    from datetime import timedelta
    data    = request.get_json()
    inc_id  = data.get("incident_id")
    tech_id = data.get("technicien_id")
    if not inc_id or not tech_id:
        return err("incident_id et technicien_id requis")
    inc  = query_one("SELECT * FROM fact_incidents WHERE id = %s", (inc_id,))
    tech = query_one("SELECT * FROM dim_technicien  WHERE id = %s", (tech_id,))
    if not inc:  return err("Incident non trouvé", 404)
    if not tech: return err("Technicien non trouvé", 404)
    dist = round(111.0 * ((
        (float(tech["latitude"]) - float(inc["latitude"]))**2 +
        (float(tech["longitude"]) - float(inc["longitude"]))**2
    )**0.5), 1)
    now = pg_now()  # horloge PostgreSQL, pas Python
    duree_min = inc.get("duree_estimee_min") or 60
    date_assignation = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    # Connexion dédiée pour garantir le commit J+1
    import psycopg2 as _pg; import psycopg2.extras as _pext
    _db = _pg.connect(host=DB_CONFIG["host"], port=DB_CONFIG["port"],
        dbname=DB_CONFIG["dbname"], user=DB_CONFIG["user"], password=DB_CONFIG["password"],
        cursor_factory=_pext.RealDictCursor, options="-c timezone=Africa/Casablanca")
    _cur = _db.cursor()
    try:
        _cur.execute("""
            INSERT INTO fact_tickets
                (code_ticket, incident_id, technicien_id, secteur_id, severite,
                 statut, statut_terrain, date_creation, date_assignation,
                 distance_km, duree_estimee_min, updated_by)
            VALUES (%s,%s,%s,%s,%s,'ASSIGNE',NULL,%s,%s,%s,%s,'ADMIN')
            RETURNING id, code_ticket
        """, (f"TKT-{inc_id:04d}-{tech_id:03d}-{now.strftime('%H%M%S')}",
              inc_id, tech_id, inc["secteur_id"], inc["severite"],
              now, date_assignation, dist, duree_min))
        new = dict(_cur.fetchone())
        new_id, new_code = new["id"], new["code_ticket"]
        # Technicien reste DISPONIBLE aujourd'hui (ticket J+1)
        _cur.execute("UPDATE dim_technicien SET statut='DISPONIBLE', ticket_actif_id=%s WHERE id=%s",
                    (new_id, tech_id))
        _cur.execute("""UPDATE fact_incidents SET statut='EN_COURS', ticket_id=%s,
                       technicien_assigne_id=%s, technicien_assigne_nom=%s WHERE id=%s""",
                    (new_id, tech_id, f"{tech['prenom']} {tech['nom']}", inc_id))
        _cur.execute("""INSERT INTO ticket_historique
                       (ticket_id,code_ticket,statut_avant,statut_apres,updated_by,commentaire,updated_at)
                       VALUES (%s,%s,NULL,'ASSIGNE','ADMIN',%s,%s)""",
                    (new_id, new_code,
                     f"Assignation J+1 — reprise {date_assignation.strftime('%d/%m à %H:%M')}", now))
        _db.commit()
        _db.close()
        log.info(f"J+1 OK: {new_code} tech={tech_id} date={date_assignation}")
    except Exception as _e:
        try: _db.rollback(); _db.close()
        except: pass
        log.error(f"J+1 ERREUR: {_e}")
        return err(f"Erreur J+1: {str(_e)}", 500)
    return jsonify({"success": True, "ticket_id": new_id, "code_ticket": new_code,
                    "technicien": f"{tech['prenom']} {tech['nom']}",
                    "date_assignation": date_assignation.isoformat(),
                    "reprise_demain": True, "distance_km": dist}), 201



@app.route("/api/tickets/<int:tk_id>/pause", methods=["POST"])
def ticket_pause(tk_id):
    data   = request.get_json() or {}
    action = data.get("action", "").upper()
    tk = query_one("""
        SELECT id, code_ticket, technicien_id, statut_terrain,
               pause_debut, duree_pause_min, duree_estimee_min
        FROM fact_tickets WHERE id = %s
    """, (tk_id,))
    if not tk: return err("Ticket introuvable", 404)
    now = datetime.now()
    ancien = tk.get("statut_terrain") or "EN_COURS"

    if action == "START":
        # Vérifier si une pause a déjà été prise sur ce ticket
        if tk.get("duree_pause_min") and int(tk["duree_pause_min"]) > 0:
            return err("Une pause a déjà été prise sur ce ticket — une seule pause autorisée", 400)
        if tk.get("statut_terrain") == "EN_PAUSE":
            return err("Ticket déjà en pause", 400)
        # Mettre EN_PAUSE + enregistrer heure debut
        execute("""
            UPDATE fact_tickets
            SET statut_terrain = 'EN_PAUSE',
                pause_debut    = %s
            WHERE id = %s
        """, (now, tk_id))
        # Mettre à jour dim_technicien.statut = EN_PAUSE
        try:
            execute("UPDATE dim_technicien SET statut='EN_PAUSE' WHERE ticket_actif_id=%s", (tk_id,))
        except Exception: pass
        # Historique
        execute("""
            INSERT INTO ticket_historique
            (ticket_id, code_ticket, statut_avant, statut_apres, updated_by, commentaire, updated_at)
            VALUES (%s, %s, %s, 'EN_PAUSE', 'TECHNICIEN', 'Pause prise par le technicien', %s)
        """, (tk_id, tk.get("code_ticket"), ancien, now))
        log.info(f"Ticket {tk_id} EN_PAUSE à {now.strftime('%H:%M')}")
        return ok({"success": True, "status": "EN_PAUSE", "pause_debut": now.isoformat()})

    elif action == "END":
        # Calculer durée réelle depuis pause_debut en DB
        pb = tk.get("pause_debut")
        if pb:
            if isinstance(pb, datetime): pb_dt = pb
            else:
                try: pb_dt = datetime.fromisoformat(str(pb))
                except: pb_dt = None
            if pb_dt and pb_dt.tzinfo:
                pb_dt = pb_dt.replace(tzinfo=None)
            dur = max(1, int((now - pb_dt).total_seconds() / 60)) if pb_dt else int(data.get("duree_min", 15))
        else:
            dur = int(data.get("duree_min", 15))

        # Remettre EN_COURS + cumuler duree_pause_min
        execute("""
            UPDATE fact_tickets
            SET statut_terrain   = 'EN_COURS',
                duree_pause_min  = COALESCE(duree_pause_min, 0) + %s,
                duree_estimee_min = duree_estimee_min + %s,
                pause_debut      = NULL
            WHERE id = %s
        """, (dur, dur, tk_id))
        # Remettre dim_technicien.statut = EN_INTERVENTION
        try:
            execute("UPDATE dim_technicien SET statut='EN_INTERVENTION' WHERE ticket_actif_id=%s", (tk_id,))
        except Exception: pass
        # Historique
        execute("""
            INSERT INTO ticket_historique
            (ticket_id, code_ticket, statut_avant, statut_apres, updated_by, commentaire, updated_at)
            VALUES (%s, %s, 'EN_PAUSE', 'EN_COURS', 'TECHNICIEN', %s, %s)
        """, (tk_id, tk.get("code_ticket"), f"Fin pause — {dur} min ajoutées à la durée estimée", now))
        log.info(f"Ticket {tk_id} reprise après {dur} min — duree_estimee allongée")
        return ok({"success": True, "status": "EN_COURS", "duree_pause_ajoutee": dur})

    return err("Action invalide — START ou END")

@app.route("/api/tickets/<int:tk_id>/forcer-cloture", methods=["POST"])
def forcer_cloture(tk_id):
    from email.utils import parsedate_to_datetime
    now = datetime.now()
    tk = query_one("""SELECT tk.*, t.nom, t.prenom FROM fact_tickets tk
                      JOIN dim_technicien t ON tk.technicien_id = t.id WHERE tk.id = %s""", (tk_id,))
    if not tk: return err("Ticket non trouvé", 404)
    if tk.get("statut_terrain") in ("COMPLETE", "INCOMPLET"):
        return ok({"already_closed": True})
    da = tk.get("date_assignation")
    if da:
        if isinstance(da, str):
            try: da = datetime.fromisoformat(da)
            except:
                try: da = parsedate_to_datetime(da).replace(tzinfo=None)
                except: da = None
        if da and hasattr(da, 'tzinfo') and da.tzinfo: da = da.replace(tzinfo=None)
    duree_reelle = max(1, int((now - da).total_seconds() / 60)) if da else None
    execute("""UPDATE fact_tickets SET statut='INCOMPLET', statut_terrain='INCOMPLET',
               cause_incomplete='Durée estimée dépassée — clôture automatique NOC',
               date_fin_intervention=%s, duree_intervention_min=%s, updated_by='SYSTEME'
               WHERE id=%s""", (now, duree_reelle, tk_id))
    execute("UPDATE dim_technicien SET statut='DISPONIBLE', ticket_actif_id=NULL WHERE id=%s",
            (tk["technicien_id"],))
    execute("""INSERT INTO ticket_historique
               (ticket_id,code_ticket,statut_avant,statut_apres,cause,commentaire,updated_by,updated_at)
               VALUES (%s,%s,%s,'INCOMPLET','Durée dépassée','Clôture forcée','SYSTEME',%s)""",
            (tk_id, tk["code_ticket"], tk.get("statut_terrain"), now))
    return ok({"success": True, "ticket_id": tk_id})


@app.route("/api/incidents/evolution")
def incidents_evolution():
    return ok(query("""
        SELECT DATE(date_detection) AS jour, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE statut IN ('RESOLU','FERME')) AS resolus,
               COUNT(*) FILTER (WHERE severite='CRITIQUE') AS critiques,
               COUNT(*) FILTER (WHERE severite='MAJEUR') AS majeurs
        FROM fact_incidents
        WHERE date_detection >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY DATE(date_detection) ORDER BY jour
    """))



@app.route("/api/incidents/<int:inc_id>/debloquer", methods=["POST"])
def debloquer_incident(inc_id):
    """Résoudre la cause bloquante — remet l'incident OUVERT pour redispatch."""
    inc = query_one("SELECT * FROM fact_incidents WHERE id = %s", (inc_id,))
    if not inc:
        return err("Incident non trouvé", 404)
    if inc.get("statut") != "INCOMPLET":
        return err("Incident non bloqué — statut actuel : " + str(inc.get("statut")), 400)
    now = datetime.now()
    execute("""UPDATE fact_incidents
               SET statut='OUVERT',
                   ticket_id=NULL,
                   technicien_assigne_id=NULL,
                   technicien_assigne_nom=NULL
               WHERE id=%s""", (inc_id,))
    log.info(f"Incident {inc_id} débloqué → OUVERT")
    return ok({"success": True, "incident_id": inc_id,
               "code_incident": inc.get("code_incident"),
               "message": "Incident remis OUVERT — prêt pour redispatch"})

@app.route("/api/incidents/escalade")
def incidents_escalade():
    return ok(query("""
        SELECT i.id, i.code_incident, i.type_incident, i.severite, i.statut,
               i.date_detection, s.nom AS secteur_nom,
               ROUND(EXTRACT(EPOCH FROM (NOW()-i.date_detection))/60) AS minutes_attente
        FROM fact_incidents i JOIN dim_secteur s ON i.secteur_id = s.id
        WHERE i.severite IN ('CRITIQUE','MAJEUR') AND i.statut='OUVERT'
          AND i.technicien_assigne_id IS NULL
          AND NOW() > i.date_detection + INTERVAL '30 minutes'
        ORDER BY i.date_detection ASC
    """))


@app.route("/api/performance/techniciens")
def performance_techniciens():
    return ok(query("""
        SELECT t.id, t.matricule, t.nom, t.prenom, s.nom AS secteur,
               t.note_perf, t.annees_exp,
               COUNT(tk.id) AS tickets_total,
               COUNT(tk.id) FILTER (WHERE tk.statut_terrain='COMPLETE') AS tickets_completes,
               COUNT(tk.id) FILTER (WHERE tk.statut_terrain='INCOMPLET') AS tickets_incomplets,
               ROUND(AVG(tk.duree_intervention_min)) AS duree_moy_min,
               ROUND(100.0*COUNT(tk.id) FILTER(WHERE tk.statut_terrain='COMPLETE')
                   /NULLIF(COUNT(tk.id),0),1) AS taux_completion
        FROM dim_technicien t
        LEFT JOIN dim_secteur s ON t.secteur_id = s.id
        LEFT JOIN fact_tickets tk ON t.id = tk.technicien_id
        GROUP BY t.id,t.matricule,t.nom,t.prenom,s.nom,t.note_perf,t.annees_exp
        ORDER BY tickets_completes DESC NULLS LAST
    """))


@app.route("/api/performance")
def performance():
    return ok(query("""
        SELECT t.id, t.matricule, t.nom, t.prenom, t.secteur_id,
               s.nom AS secteur, t.statut, t.note_perf, t.annees_exp,
               COUNT(tk.id)                                              AS tickets_total,
               COUNT(tk.id) FILTER (WHERE tk.statut_terrain='COMPLETE') AS tickets_completes,
               COUNT(tk.id) FILTER (WHERE tk.statut_terrain='INCOMPLET')AS tickets_incomplets,
               ROUND(AVG(tk.duree_intervention_min))                     AS duree_moy_min,
               ROUND(AVG(tk.note_intervention), 1)                       AS note_moyenne
        FROM dim_technicien t
        LEFT JOIN dim_secteur  s  ON t.secteur_id = s.id
        LEFT JOIN fact_tickets tk ON t.id = tk.technicien_id
        GROUP BY t.id,t.matricule,t.nom,t.prenom,t.secteur_id,s.nom,t.statut,t.note_perf,t.annees_exp
        ORDER BY tickets_completes DESC NULLS LAST
    """))

# ═══════════════════════════════════════════════════════════════
# INDEX
# ═══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return """<html><head><title>API BI Telecom</title>
    <style>body{font-family:monospace;background:#050a0f;color:#c8dde8;padding:40px}
    h1{color:#00d4ff}a{color:#00ff88;display:block;padding:2px 0}</style></head><body>
    <h1>API BI Telecom v3</h1>
    <a href="/api/health">/api/health</a>
    <a href="/api/kpi">/api/kpi</a>
    <a href="/api/carte">/api/carte</a>
    <a href="/api/incidents">/api/incidents</a>
    <a href="/api/techniciens">/api/techniciens</a>
    <a href="/api/gantt">/api/gantt</a>
    <a href="/api/performance">/api/performance</a>
    </body></html>"""

if __name__ == "__main__":
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  API BI Télécom v3 — sans auth                  ║")
    print(f"║  DB: {DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print("║  http://localhost:5000                          ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # Test connexion au démarrage
    try:
        import psycopg2 as pg
        conn = pg.connect(**DB_CONFIG)
        print("✅ PostgreSQL connecté")
        conn.close()
    except Exception as e:
        print(f"❌ PostgreSQL erreur : {e}")
        print("   → Vérifie le mot de passe dans DB_CONFIG")

    app.run(debug=True, port=5000)