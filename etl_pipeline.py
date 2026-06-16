"""
etl_pipeline.py — v3
ETL Pipeline — Plateforme BI Télécom Huawei
PFE 2026

- Pas d'authentification (pas de dim_users)
- Incidents : tous chargés avec statut OUVERT
- Tickets   : table vide (créés uniquement via dispatch)
- Compatible avec les CSV existants (colonne 'code' ou 'code_incident')
- duree_estimee_min calculée depuis le type d'incident si absente
"""

import json, csv, os, sys, logging
from datetime import datetime, date, timedelta
from pathlib import Path

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "dbname":   os.getenv("DB_NAME",     "bi_telecom"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

DATA_DIR = Path("data")

# Durée estimée par défaut selon type d'incident (minutes)
# Plages réalistes par type (min, max en minutes) — max 4h
DUREE_ESTIMEE_PLAGES = {
    "PANNE_COMPLETE": (180, 240),  # 3h – 4h
    "COUPURE_FIBRE":  (150, 180),  # 2h30 – 3h
    "PANNE_ALIM":     (120, 180),  # 2h – 3h
    "DEGRADATION":    ( 60, 120),  # 1h – 2h
    "SURCHARGE":      ( 45,  90),  # 45min – 1h30
    "INTERFERENCE":   ( 30,  60),  # 30min – 1h
    "ALARME_RESEAU":  ( 30,  60),  # 30min – 1h
    "MISE_A_JOUR":    ( 45,  90),  # 45min – 1h30
}

def get_duree_estimee(type_inc, severite, seed=None):
    import random
    plage = DUREE_ESTIMEE_PLAGES.get(type_inc)
    if plage:
        rng = random.Random(seed) if seed else random
        return rng.randint(plage[0], plage[1])
    return {"CRITIQUE":210,"MAJEUR":120,"MINEUR":60}.get(severite, 90)
DUREE_PAR_SEVERITE = {"CRITIQUE": 240, "MAJEUR": 120, "MINEUR": 60}

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
DATA_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/etl.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("ETL")

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def load_csv(filename):
    path = DATA_DIR / filename
    rows = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    log.info(f"  Extrait  : {filename} — {len(rows)} lignes")
    return rows

def parse_dt(val):
    if not val or str(val).strip() in ("", "None", "null"): return None
    try: return datetime.fromisoformat(str(val).strip())
    except: return None

def parse_float(val):
    try: return float(val) if val and str(val).strip() not in ("", "None") else None
    except: return None

def parse_int(val):
    try:
        v = str(val).strip()
        return int(float(v)) if v not in ("", "None", "null") else None
    except: return None

def parse_bool(val):
    if isinstance(val, bool): return val
    return str(val).strip().lower() in ("true", "1", "yes")

def parse_list(val):
    try: return json.loads(val) if val else []
    except: return []

def col(row, *keys, default=""):
    """Cherche la première clé existante dans la ligne CSV."""
    for k in keys:
        if k in row and row[k] not in ("", None, "None", "null"):
            return row[k]
    return default

# ─────────────────────────────────────────────
# E — EXTRACT
# ─────────────────────────────────────────────
def extract():
    log.info("━━━ EXTRACT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return {
        "secteurs":    load_csv("secteurs.csv"),
        "equipements": load_csv("equipements.csv"),
        "techniciens": load_csv("techniciens.csv"),
        "incidents":   load_csv("incidents.csv"),
    }

# ─────────────────────────────────────────────
# T — TRANSFORM
# ─────────────────────────────────────────────
def transform(raw):
    log.info("━━━ TRANSFORM ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # ── dim_secteur ──
    secteurs = []
    for r in raw["secteurs"]:
        secteurs.append({
            "id":        parse_int(r["id"]),
            "nom":       r["nom"].strip(),
            "region":    r["region"].strip(),
            "latitude":  parse_float(col(r, "lat", "latitude")),
            "longitude": parse_float(col(r, "lng", "longitude")),
        })
    log.info(f"  Secteurs transformés    : {len(secteurs)}")

    # ── dim_equipement ──
    equipements = []
    for r in raw["equipements"]:
        equipements.append({
            "id":           parse_int(r["id"]),
            "code":         r["code"].strip(),
            "nom":          r["nom"].strip(),
            "type":         r["type"].strip(),
            "libelle_type": r.get("libelle_type","").strip(),
            "marque":       r.get("marque","Huawei").strip(),
            "modele":       r.get("modele","").strip(),
            "secteur_id":   parse_int(r["secteur_id"]),
            "latitude":     parse_float(r.get("latitude")),
            "longitude":    parse_float(r.get("longitude")),
            "date_install": col(r, "date_install") or None,
            "statut":       r.get("statut","ACTIF").strip(),
        })
    log.info(f"  Équipements transformés : {len(equipements)}")

    # ── dim_technicien ──
    techniciens = []
    for r in raw["techniciens"]:
        techniciens.append({
            "id":                     parse_int(r["id"]),
            "matricule":              r["matricule"].strip(),
            "nom":                    r["nom"].strip(),
            "prenom":                 r["prenom"].strip(),
            "telephone":              r.get("telephone","").strip(),
            "secteur_id":             parse_int(r["secteur_id"]),
            "latitude":               parse_float(r.get("latitude")),
            "longitude":              parse_float(r.get("longitude")),
            "competences":            ", ".join(parse_list(r.get("competences","[]"))),
            "statut":                 r.get("statut","DISPONIBLE").strip(),
            "annees_exp":             parse_int(r.get("annees_exp", 1)),
            "note_perf":              parse_float(r.get("note_perf", 3.5)),
            "heure_debut":            col(r, "heure_debut", default="09:00"),
            "heure_fin":              col(r, "heure_fin",   default="18:00"),
            "capacite_tickets":       parse_int(r.get("capacite_tickets", 1)) or 1,
            "ticket_actif_id":        None,   # toujours NULL au chargement
            "raison_indisponibilite": col(r, "raison_indisponibilite") or None,
        })
    log.info(f"  Techniciens transformés : {len(techniciens)}")

    # ── dim_temps ──
    dates_set = set()
    for r in raw["incidents"]:
        dt = parse_dt(r.get("date_detection",""))
        if dt: dates_set.add(dt.date())
    today = date.today()
    for i in range(14):          # aujourd'hui + 13 jours pour le Gantt
        dates_set.add(today + timedelta(days=i))
    if dates_set:
        d_min, d_max = min(dates_set), max(dates_set)
        cur = d_min
        while cur <= d_max:
            dates_set.add(cur)
            cur += timedelta(days=1)
    dim_temps = []
    for i, d in enumerate(sorted(dates_set), start=1):
        dim_temps.append({
            "id":          i,
            "date_jour":   d.isoformat(),
            "annee":       d.year,
            "mois":        d.month,
            "semaine":     d.isocalendar()[1],
            "jour_sem":    d.weekday(),
            "trimestre":   (d.month - 1) // 3 + 1,
            "est_weekend": d.weekday() >= 5,
        })
    log.info(f"  Dim_temps générée       : {len(dim_temps)} jours")

    # ── fact_incidents — TOUS OUVERT ──
    incidents = []
    erreurs   = 0
    for r in raw["incidents"]:
        try:
            date_det  = parse_dt(r["date_detection"])
            type_inc  = r.get("type_incident","").strip()
            severite  = r.get("severite","MINEUR").strip()

            # code_incident : compatible ancien format ("code") et nouveau ("code_incident")
            code_inc  = col(r, "code_incident", "code")

            # duree_estimee_min : depuis CSV si dispo, sinon calculée par type (plage réaliste)
            duree_est = parse_int(r.get("duree_estimee_min"))
            if not duree_est:
                duree_est = get_duree_estimee(type_inc, severite, seed=parse_int(r.get('id')))

            # priorite : depuis CSV ou déduite
            priorite = parse_int(r.get("priorite"))
            if not priorite:
                priorite = {"CRITIQUE":1,"MAJEUR":2,"MINEUR":3}.get(severite, 3)

            incidents.append({
                "id":                    parse_int(r["id"]),
                "code_incident":         code_inc,
                "equipement_id":         parse_int(r.get("equipement_id")),
                "secteur_id":            parse_int(r.get("secteur_id")),
                "type_incident":         type_inc,
                "libelle":               r.get("libelle","").strip(),
                "severite":              severite,
                "statut":                "OUVERT",       # forcé OUVERT
                "date_detection":        date_det,
                "date_resolution":       None,
                "duree_resolution_min":  None,
                "duree_estimee_min":     duree_est,
                "latitude":              parse_float(r.get("latitude")),
                "longitude":             parse_float(r.get("longitude")),
                "description":           r.get("description","").strip(),
                "priorite":              priorite,
                "ticket_id":             None,
                "technicien_assigne_id": None,
                "technicien_assigne_nom":None,
            })
        except Exception as e:
            erreurs += 1
            log.warning(f"  Incident ignoré (id={r.get('id','?')}) : {e}")

    critiques = sum(1 for i in incidents if i["severite"] == "CRITIQUE")
    log.info(f"  Incidents transformés   : {len(incidents)} (erreurs: {erreurs})")
    log.info(f"  Statut forcé OUVERT     : prêts pour dispatch")
    log.info(f"  Incidents critiques     : {critiques} / {len(incidents)}")
    log.info(f"  duree_estimee_min moy.  : {round(sum(i['duree_estimee_min'] for i in incidents)/len(incidents))} min")

    return {
        "dim_secteur":    secteurs,
        "dim_equipement": equipements,
        "dim_technicien": techniciens,
        "dim_temps":      dim_temps,
        "fact_incidents": incidents,
    }

# ─────────────────────────────────────────────
# L — LOAD
# ─────────────────────────────────────────────
def load(data, mode="file"):
    log.info("━━━ LOAD ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if mode == "file":
        _load_to_file(data)
    elif mode == "db":
        _load_to_db(data)
    else:
        raise ValueError(f"mode inconnu : {mode}")

def _val(v):
    if v is None:                       return "NULL"
    if isinstance(v, bool):             return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):     return str(v)
    if isinstance(v, datetime):         return f"'{v.isoformat()}'"
    s = str(v).replace("'","''")
    return f"'{s}'"

def _insert_block(table, rows):
    if not rows:
        return f"\n-- {table} : vide\n"
    cols = ", ".join(rows[0].keys())
    lines = [f"  ({', '.join(_val(v) for v in row.values())})" for row in rows]
    return (
        f"\n-- {table} ({len(rows)} lignes)\n"
        f"INSERT INTO {table} ({cols}) VALUES\n" +
        ",\n".join(lines) +
        "\nON CONFLICT DO NOTHING;\n"
    )

def _load_to_file(data):
    path = DATA_DIR / "dwh_inserts.sql"

    def val(v):
        """Sérialise une valeur Python en SQL compatible pgAdmin."""
        if v is None:                   return "NULL"
        if isinstance(v, bool):         return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)): return str(v)
        if isinstance(v, datetime):     return f"'{v.isoformat()}'"
        return "'" + str(v).replace("'", "''") + "'"

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"-- DWH BI Telecom Huawei — {datetime.now():%Y-%m-%d %H:%M:%S}\n\n")

        # Nettoyage dans l'ordre inverse des FK
        f.write("-- Nettoyage avant insertion\n")
        for t in ["fact_tickets","ticket_historique","fact_incidents",
                  "dim_temps","dim_technicien","dim_equipement","dim_secteur"]:
            f.write(f"DELETE FROM {t};\n")
        f.write("\n")

        ordre = ["dim_secteur","dim_equipement","dim_technicien",
                 "dim_temps","fact_incidents"]

        for table in ordre:
            rows = data.get(table, [])
            if not rows:
                continue
            cols = list(rows[0].keys())
            col_str = ", ".join(cols)
            f.write(f"-- {table} ({len(rows)} lignes)\n")
            for row in rows:
                vals = ", ".join(val(row[c]) for c in cols)
                f.write(f"INSERT INTO {table} ({col_str}) VALUES ({vals});\n")
            f.write("\n")
            log.info(f"  Chargé (SQL) : {table} — {len(rows)} lignes")

    size = path.stat().st_size // 1024
    log.info(f"  Fichier SQL  : {path} ({size} Ko)")

def _load_to_db(data):
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)
        cur  = conn.cursor()

        # Créer / vérifier schéma
        schema = DATA_DIR / "schema_dwh.sql"
        if schema.exists():
            cur.execute(schema.read_text(encoding="utf-8"))
            conn.commit()
            log.info("  Schéma créé / vérifié")
        else:
            log.warning("  schema_dwh.sql introuvable — les tables doivent déjà exister")

        ordre = [
            "dim_secteur",
            "dim_equipement",
            "dim_technicien",
            "dim_temps",
            "fact_incidents",
        ]

        for table in ordre:
            rows = data.get(table, [])
            if not rows:
                log.info(f"  Skipped : {table} (vide)")
                continue
            cols         = list(rows[0].keys())
            placeholders = ", ".join(["%s"] * len(cols))
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
            import psycopg2.extras as ex
            ex.execute_batch(cur, sql, [list(r.values()) for r in rows], page_size=200)
            conn.commit()
            log.info(f"  Inséré DB : {table} — {len(rows)} lignes")

        cur.close()
        conn.close()
        log.info("  Connexion fermée")

    except ImportError:
        log.error("  psycopg2 non installé — pip install psycopg2-binary")
    except Exception as e:
        log.error(f"  Erreur DB : {e}")
        raise

# ─────────────────────────────────────────────
# RAPPORT
# ─────────────────────────────────────────────
def rapport_etl(data):
    log.info("━━━ RAPPORT ETL ━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    inc   = data["fact_incidents"]
    techs = data["dim_technicien"]

    sev = {}
    for i in inc: sev[i["severite"]] = sev.get(i["severite"],0)+1

    heures = {}
    for i in inc:
        if i["date_detection"]:
            h = i["date_detection"].hour
            heures[h] = heures.get(h,0)+1
    heure_pic = max(heures, key=heures.get) if heures else "?"

    durees = [i["duree_estimee_min"] for i in inc if i["duree_estimee_min"]]
    duree_moy = round(sum(durees)/len(durees)) if durees else 0
    dispo  = sum(1 for t in techs if t["statut"]=="DISPONIBLE")
    indispo= sum(1 for t in techs if t["statut"]=="INDISPONIBLE")

    log.info(f"  Incidents OUVERT        : {len(inc)}")
    log.info(f"  Sévérité                : {sev}")
    log.info(f"  Durée estimée moy.      : {duree_moy} min")
    log.info(f"  Heure pic               : {heure_pic}h")
    log.info(f"  Techniciens dispo       : {dispo} / {len(techs)}")
    log.info(f"  Indisponibles           : {indispo}")
    log.info(f"  Tickets                 : 0 (créés via dispatch)")

    rapport = {
        "date_etl": datetime.now().isoformat(),
        "nb_secteurs": len(data["dim_secteur"]),
        "nb_equipements": len(data["dim_equipement"]),
        "nb_techniciens": len(techs),
        "nb_incidents": len(inc),
        "nb_tickets": 0,
        "kpi": {
            "incidents_ouverts": len(inc),
            "duree_estimee_moy_min": duree_moy,
            "techniciens_disponibles": dispo,
            "techniciens_indisponibles": indispo,
        },
        "repartition_severite": sev,
        "heure_pic_incidents": heure_pic,
    }

    rp = DATA_DIR / "rapport_etl.json"
    with open(rp,"w",encoding="utf-8") as f:
        json.dump(rapport,f,ensure_ascii=False,indent=2,default=str)
    log.info(f"  Rapport JSON : {rp}")
    return rapport

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def run(load_mode="file"):
    start = datetime.now()
    log.info("╔══════════════════════════════════════════════╗")
    log.info("║  ETL BI Télécom v3 — démarrage              ║")
    log.info("╚══════════════════════════════════════════════╝")
    raw     = extract()
    data    = transform(raw)
    load(data, mode=load_mode)
    rapport = rapport_etl(data)
    log.info(f"━━━ TERMINÉ en {round((datetime.now()-start).total_seconds(),2)}s ━━━━━━━━━━━━━━━━━━━━━━━━")
    return data, rapport

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "file"
    run(load_mode=mode)