-- Step 1 — how big one table is. Run once per table from step 1.
-- Reads: a count. No values leave the database.
-- Why: every list this skill writes states its total, and "a few" is not a total.
-- Read-only.
SELECT COUNT(*) AS row_count FROM {{table.table}};
