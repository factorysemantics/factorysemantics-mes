-- Step 4 — the shape of one column. Run once per column you care about.
-- Reads: counts and lengths. Not one stored value.
-- Why: null share and distinct count tell you whether a column is a key, a
--      vocabulary, a measurement or an empty field somebody never filled in.
-- Read-only.
SELECT COUNT_BIG(*)                                          AS rows_seen,
       SUM(CASE WHEN {{table.column}} IS NULL THEN 1 ELSE 0 END) AS nulls,
       COUNT(DISTINCT {{table.column}})                      AS distinct_values,
       MIN(LEN(CAST({{table.column}} AS NVARCHAR(4000))))    AS min_length,
       MAX(LEN(CAST({{table.column}} AS NVARCHAR(4000))))    AS max_length
FROM {{table.table}};
