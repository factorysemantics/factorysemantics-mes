-- Step 5 — the machine list: how many, in what order, on which line.
-- Reads: the asset register. Codes come back here and are NOT written out —
--        the output names stations ST-01, ST-02 … in line order.
-- Why: the number of stations and their order is the skeleton of the plant.
-- Read-only, bounded.
SELECT a.{{assets.id}}           AS asset_key,
       a.{{assets.code}}         AS asset_code,
       {{assets.line?@a}}        AS line_code,
       {{assets.sequence?@a}}    AS seq
FROM {{assets.table}} a
ORDER BY 4, 2
LIMIT {{row_cap}};
