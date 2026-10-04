-- Step 5 — the same bounded tag sample as 05b, for a plant whose MES does not
-- keep closed state intervals (no `until` column to join on).
--
-- Use this one ONLY when 05b cannot be filled in. Without the machine's state
-- beside each sample, a tag's base is the median of everything rather than the
-- median of what it reads while the machine runs, and "this tag only moves when
-- the machine runs" cannot be measured at all. The output says so: the tag's
-- `modal_state` comes back "unknown".
--
-- Reads: tag path, unit, timestamp, numeric value. Nothing identifying.
-- Read-only, bounded.
SELECT d.{{tags.asset}}    AS asset_key,
       d.{{tags.path}}     AS tag_path,
       {{tags.unit?@d}}    AS eng_unit,
       s.{{samples.at}}    AS sample_at,
       s.{{samples.value}} AS num_value,
       NULL                AS state_word
FROM {{samples.table}} s
JOIN {{tags.table}} d
  ON d.{{tags.id}} = s.{{samples.tag}}
ORDER BY s.{{samples.at}} DESC
LIMIT {{row_cap}};
