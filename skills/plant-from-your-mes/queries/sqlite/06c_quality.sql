-- Step 6 — the quality loop: how often a check happens, how often it fails, how
-- often a failure becomes a non-conformance, and what happens to those.
-- Reads: counts per verdict and per disposition, plus the characteristic count.
--        No non-conformance number, no inspector, no measured value tied to
--        anything identifiable.
-- Why: 887 open non-conformances that nobody dispositions looks exactly like a
--      plant with no quality problem until you count the dispositions.
-- Read-only, bounded.
SELECT q.{{quality.verdict}}                                       AS verdict,
       {{quality.disposition?@q}}                                  AS disposition,
       COUNT(*)                                                    AS rows_seen,
       SUM(CASE WHEN {{quality.nonconformance?@q}} IS NULL THEN 0 ELSE 1 END) AS with_nonconformance
FROM {{quality.table}} q
GROUP BY 1, 2
ORDER BY 3 DESC
LIMIT {{row_cap}};
