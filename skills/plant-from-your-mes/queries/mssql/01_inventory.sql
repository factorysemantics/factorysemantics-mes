-- Step 1 — what is here at all.
-- Reads: SQL Server's own catalogue, plus the row count the engine already
--        keeps. No plant data, no customer data.
-- Why approximate: an exact COUNT(*) over every table on a plant historian can
--      take minutes and escalate locks on a production box. For an inventory you
--      want the engine's own number. When one table's exact count matters, run
--      01b_row_count.sql against that one table.
-- Read-only. One row per table.
SELECT s.name                  AS schema_name,
       t.name                  AS table_name,
       SUM(p.row_count)        AS row_count_approx
FROM sys.tables t
JOIN sys.schemas s          ON s.schema_id = t.schema_id
JOIN sys.dm_db_partition_stats p
  ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY s.name, t.name
ORDER BY s.name, t.name;
