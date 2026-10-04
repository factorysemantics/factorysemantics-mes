-- Step 6 — good and scrap per machine over the window.
-- Reads: two sums and a count per machine. No lot, pallet, order or person.
-- Why: scrap share per station is a number the simulation needs and a number
--      the plant will recognise as theirs.
-- Read-only.
SELECT p.{{production.asset}}        AS asset_key,
       COUNT(*)                      AS bookings,
       SUM(p.{{production.good}})    AS good_total,
       SUM(p.{{production.scrap}})   AS scrap_total,
       MIN(p.{{production.at}})       AS first_seen,
       MAX(p.{{production.at}})       AS last_seen
FROM {{production.table}} p
GROUP BY p.{{production.asset}}
ORDER BY 1
LIMIT {{row_cap}};
