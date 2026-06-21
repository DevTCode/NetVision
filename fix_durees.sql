-- ═══════════════════════════════════════════════════════════════
-- FIX — Durées d'incidents aberrantes (PFE NetVision)
-- À exécuter dans pgAdmin sur la base bi_telecom
-- ═══════════════════════════════════════════════════════════════

-- 1. Vérification AVANT correction
SELECT type_incident,
       COUNT(*)                          AS nb,
       MIN(duree_estimee_min)            AS min_min,
       MAX(duree_estimee_min)            AS max_min,
       ROUND(AVG(duree_estimee_min))     AS moy_min
FROM fact_incidents
GROUP BY type_incident
ORDER BY max_min DESC;

-- 2. Correction — ramène toutes les durées aberrantes (>240min) à des
--    plages réalistes et cohérentes avec le frontend Gantt (max 4h)
UPDATE fact_incidents
SET duree_estimee_min = CASE type_incident
    WHEN 'PANNE_COMPLETE' THEN 180 + (RANDOM()*60)::INT   -- 3h00–4h00
    WHEN 'COUPURE_FIBRE'  THEN 150 + (RANDOM()*30)::INT   -- 2h30–3h00
    WHEN 'PANNE_ALIM'     THEN 120 + (RANDOM()*60)::INT   -- 2h00–3h00
    WHEN 'DEGRADATION'    THEN  60 + (RANDOM()*60)::INT   -- 1h00–2h00
    WHEN 'SURCHARGE'      THEN  45 + (RANDOM()*45)::INT   -- 0h45–1h30
    WHEN 'INTERFERENCE'   THEN  30 + (RANDOM()*30)::INT   -- 0h30–1h00
    WHEN 'ALARME_RESEAU'  THEN  30 + (RANDOM()*30)::INT   -- 0h30–1h00
    WHEN 'MISE_A_JOUR'    THEN  45 + (RANDOM()*45)::INT   -- 0h45–1h30
    ELSE duree_estimee_min
END
WHERE duree_estimee_min > 240;

-- 3. Synchroniser les tickets déjà créés (dispatchés) avec la nouvelle
--    durée de leur incident, SAUF si le ticket est déjà terminé
--    (COMPLETE/INCOMPLET) — on ne touche jamais à l'historique réel.
UPDATE fact_tickets tk
SET duree_estimee_min = fi.duree_estimee_min
FROM fact_incidents fi
WHERE tk.incident_id = fi.id
  AND tk.statut_terrain IS DISTINCT FROM 'COMPLETE'
  AND tk.statut_terrain IS DISTINCT FROM 'INCOMPLET'
  AND tk.duree_estimee_min > 240;

-- 4. Vérification APRÈS correction
SELECT type_incident,
       COUNT(*)                          AS nb,
       MIN(duree_estimee_min)            AS min_min,
       MAX(duree_estimee_min)            AS max_min,
       ROUND(AVG(duree_estimee_min))     AS moy_min
FROM fact_incidents
GROUP BY type_incident
ORDER BY max_min DESC;

-- 5. Vérification qu'aucun ticket actif n'a plus une durée aberrante
SELECT code_ticket, statut_terrain, duree_estimee_min
FROM fact_tickets
WHERE duree_estimee_min > 240
  AND statut_terrain IS DISTINCT FROM 'COMPLETE'
  AND statut_terrain IS DISTINCT FROM 'INCOMPLET';
-- Doit retourner 0 lignes
