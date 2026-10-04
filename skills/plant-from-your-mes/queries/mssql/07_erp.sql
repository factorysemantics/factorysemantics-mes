-- Step 7 — the ERP touchpoints: which calls, how often, how big, how often they
-- fail.
--
-- This is the step most likely to find nothing. Plenty of plants have no call
-- log at all — the integration is a nightly file, or a middleware box nobody
-- here administers. If this query has no table to run against, STOP AND ASK THE
-- PERSON rather than guessing: "how does this MES talk to the ERP, and is there
-- anywhere it records that it did?" Write their answer into the [erp] section's
-- `asked` note. Nothing found is reported as nothing found, never as none.
--
-- Reads: verb, endpoint, counts, byte sizes, status shares. The endpoint has
--        its ids and query string stripped before anything is written out.
-- Read-only, bounded.
SELECT TOP ({{row_cap}})
       {{erp.verb?@e}}       AS verb,
       e.{{erp.endpoint}}    AS endpoint,
       COUNT_BIG(*)          AS calls,
       MIN({{erp.bytes?@e}}) AS min_bytes,
       MAX({{erp.bytes?@e}}) AS max_bytes,
       SUM(CASE WHEN {{erp.status?@e}} >= 400 THEN 1 ELSE 0 END) AS failures
FROM {{erp.table}} e
GROUP BY {{erp.verb?@e}}, e.{{erp.endpoint}}
ORDER BY COUNT_BIG(*) DESC;
