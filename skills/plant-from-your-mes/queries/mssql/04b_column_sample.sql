-- Step 4 — a bounded sample of one text column's distinct values.
--
-- THIS IS THE ONLY QUERY IN THE SET THAT READS REAL STRINGS, and nothing it
-- returns is ever written into plant-shape.toml. Two things are done with it:
-- a *pattern* is derived (ABC-0000 from four examples, never an example), and
-- the values themselves are handed to leakcheck.py, which refuses to write the
-- output file if any of them appears in it.
--
-- Read-only, bounded.
SELECT DISTINCT TOP ({{row_cap}}) CAST({{table.column}} AS NVARCHAR(4000)) AS value
FROM {{table.table}}
WHERE {{table.column}} IS NOT NULL;
