-- Step 6 — every stop and every state, one row each, with the reason CATEGORY
-- and the LENGTH of the reason text. Never the text.
--
-- Reads: machine key, state word, start, duration, reason category, and how
--        many characters the note was. The note itself stays in the database.
-- Why: stop duration distributions, the share of time in each state, the share
--      of stops that carry a reason at all — and the share that do not, which
--      is a finding and is reported as one. The generic wording the output
--      carries is written by this skill to the measured length; it is not the
--      customer's sentence shortened.
-- Read-only, bounded.
SELECT l.{{states.asset}}                       AS asset_key,
       l.{{states.state}}                       AS state_word,
       l.{{states.at}}                          AS started_at,
       l.{{states.seconds}}                     AS seconds,
       {{reasons.category?@r}}                  AS reason_category,
       LENGTH(CAST({{states.note?@l}} AS TEXT)) AS note_length
FROM {{states.table}} l
LEFT JOIN {{reasons.table}} r
  ON r.{{reasons.id}} = l.{{states.reason}}
ORDER BY l.{{states.at}} DESC
LIMIT {{row_cap}};
