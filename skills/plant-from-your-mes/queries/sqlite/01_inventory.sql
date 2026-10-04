-- Step 1 — what is here at all.
-- Reads: the database's own catalogue. No plant data, no customer data.
-- Why: you cannot ask a sensible question of a schema you have not listed.
-- Read-only. Returns one row per table.
SELECT name AS table_name
FROM sqlite_master
WHERE type = 'table'
  AND name NOT LIKE 'sqlite_%'
ORDER BY name;
