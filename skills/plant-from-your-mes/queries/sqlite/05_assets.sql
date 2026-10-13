-- Step 5 — the machine list: how many, in what order, on which line.
-- Reads: the asset register. Codes come back here and are NOT written out —
--        the output names stations ST-01, ST-02 … in line order.
-- Why: the number of stations and their order is the skeleton of the plant.
--      The line and cell columns say which area each machine sits in. Their
--      values are counted and discarded like the codes: the output names
--      areas AR-01, AR-02 … and cells CL-01, CL-02 … in first-seen order.
-- Read-only, bounded.
SELECT a.{{assets.id}}           AS asset_key,
       a.{{assets.code}}         AS asset_code,
       {{assets.line?@a}}        AS line_code,
       {{assets.cell?@a}}        AS cell_code,
       {{assets.sequence?@a}}    AS seq
FROM {{assets.table}} a
ORDER BY 5, 2
LIMIT {{row_cap}};
