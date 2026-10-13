-- Step 5 — the routings: which machines an order goes through, and in what order.
-- Reads: the routing (or operation) table — a routing key, the machine each
--        step is done on, and the step number. Keys come back here and are NOT
--        written out: the output names routings RT-01, RT-02 … and refers to
--        machines as ST-01, ST-02 … like everything else.
-- Why: format 1 could only say "one straight line". Two cells feeding one
--      shared machine, or a routing that differs by material, is the shape of
--      most real plants and cannot be guessed from a machine list. A plant
--      whose MES has no such table says so — the shape file then carries one
--      straight routing and says it was never measured.
-- Read-only, bounded.
SELECT r.{{routings.code}}      AS routing_key,
       r.{{routings.asset}}     AS asset_key,
       {{routings.sequence?@r}} AS seq
FROM {{routings.table}} r
ORDER BY 1, 3
LIMIT {{row_cap}};
