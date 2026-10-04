-- Step 6 — order sizes and how long an order is open.
-- Reads: the ordered quantity and the two timestamps, bounded. Not the order
--        number, not the item, not the customer.
-- Why: a simulation that runs one endless order does not look like a plant.
-- Read-only, bounded.
SELECT TOP ({{row_cap}})
       o.{{orders.quantity}} AS qty_ordered,
       o.{{orders.opened}}   AS opened_at,
       {{orders.closed?@o}}  AS closed_at
FROM {{orders.table}} o
ORDER BY o.{{orders.opened}} DESC;
