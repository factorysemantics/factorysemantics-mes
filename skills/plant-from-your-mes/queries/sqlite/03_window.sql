-- Step 3 — when this table was written, and how often. Run once per table that
-- has a timestamp column (you found them in step 2).
-- Reads: the first and last timestamp, the row count, and the number of
--        distinct calendar days. Four numbers and two timestamps.
-- Why: a plant shape built from an unknown window is a guess. This is also how
--      you find the table somebody stopped writing to in 2019.
-- Read-only.
SELECT MIN({{table.at}})                              AS first_seen,
       MAX({{table.at}})                              AS last_seen,
       COUNT(*)                                       AS rows_seen,
       COUNT(DISTINCT substr(CAST({{table.at}} AS TEXT), 1, 10)) AS distinct_days
FROM {{table.table}};
