-- Step 1 — the exact size of one table. Run it on the tables that matter, not
-- on all of them (see 01_inventory.sql for why).
-- Reads: a count. No values leave the database.
-- Read-only.
SELECT COUNT_BIG(*) AS row_count FROM {{table.table}};
