-- Step 6 — the shift pattern.
-- Reads: shift code and the two times of day, bounded. Not the crew, not a name.
-- Why: three eight-hour shifts and two twelve-hour shifts are different plants,
--      and every per-shift number later depends on knowing which.
-- Read-only, bounded.
SELECT s.{{shifts.code}}   AS shift_code,
       s.{{shifts.starts}} AS starts_at,
       s.{{shifts.ends}}   AS ends_at
FROM {{shifts.table}} s
ORDER BY s.{{shifts.starts}} DESC
LIMIT {{row_cap}};
