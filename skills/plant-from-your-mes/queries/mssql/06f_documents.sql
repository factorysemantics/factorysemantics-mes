-- Step 6 — the document numbering convention.
-- Reads: document numbers and revisions, bounded. The numbers are used to
--        derive a PATTERN (SOP-QA-### from nine examples) and are then handed to
--        leakcheck.py. No title, no content, no number reaches the output.
-- Why: Scott asked for this by name. A plant recognises its own numbering
--      grammar instantly, and a demo that invents one reads as somebody else's.
-- Read-only, bounded.
SELECT TOP ({{row_cap}})
       d.{{documents.number}}    AS doc_number,
       {{documents.revision?@d}} AS revision
FROM {{documents.table}} d;
