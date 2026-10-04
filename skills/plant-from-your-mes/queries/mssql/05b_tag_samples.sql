-- Step 5 — the per-tag statistics, from a bounded sample of recent history with
-- the machine's state resolved for each sample.
--
-- Reads: tag path, unit, timestamp, numeric value, and the state word the
--        machine was in at that moment. Numbers and tag paths; no order, lot,
--        pallet or person. The tag path is used to group and is not written out:
--        the output names tags AN-01, AN-02 … with their unit and their kind.
-- Why: this is the one query the simulation is actually built from. Base,
--      spread, sampling interval, whether a tag only moves while the machine
--      runs — all of it comes out of these rows, in profile.py, not in SQL.
-- Before you run this on a production box: this is the big table. It wants an
--      index on the timestamp column, and the plan should be a backward scan of
--      that index, not a sort. Check the plan first, run it out of hours, and if
--      it is still heavy add a WHERE on the timestamp for one week and say in
--      the output that the window was one week.
-- Read-only, bounded.
SELECT TOP ({{row_cap}})
       d.{{tags.asset}}    AS asset_key,
       d.{{tags.path}}     AS tag_path,
       {{tags.unit?@d}}    AS eng_unit,
       s.{{samples.at}}    AS sample_at,
       s.{{samples.value}} AS num_value,
       {{states.state?@l}} AS state_word
FROM {{samples.table}} s
JOIN {{tags.table}} d
  ON d.{{tags.id}} = s.{{samples.tag}}
LEFT JOIN {{states.table}} l
  ON l.{{states.asset}} = d.{{tags.asset}}
 AND s.{{samples.at}} >= l.{{states.at}}
 AND s.{{samples.at}} <  l.{{states.until}}
ORDER BY s.{{samples.at}} DESC;
