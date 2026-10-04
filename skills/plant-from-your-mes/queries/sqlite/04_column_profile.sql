-- Step 4 — the shape of one column. Run once per column you care about.
-- Reads: counts and lengths. Not one stored value.
-- Why: null share and distinct count tell you whether a column is a key, a
--      vocabulary, a measurement or an empty field somebody never filled in.
-- Read-only.
SELECT COUNT(*)                                             AS rows_seen,
       SUM(CASE WHEN {{table.column}} IS NULL THEN 1 ELSE 0 END) AS nulls,
       COUNT(DISTINCT {{table.column}})                     AS distinct_values,
       MIN(LENGTH(CAST({{table.column}} AS TEXT)))          AS min_length,
       MAX(LENGTH(CAST({{table.column}} AS TEXT)))          AS max_length
FROM {{table.table}};
