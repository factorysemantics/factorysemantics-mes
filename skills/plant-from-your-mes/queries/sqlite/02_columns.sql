-- Step 2 — the columns, their declared types, and which are keys.
-- Reads: the catalogue only. Still no plant data.
-- Why: this is the step where you work out which table is the machine list,
--      which is the tag history, and which is somebody's spreadsheet import.
-- Read-only. One row per column.
SELECT m.name                                        AS table_name,
       c.name                                        AS column_name,
       c.type                                        AS data_type,
       CASE c."notnull" WHEN 1 THEN 'no' ELSE 'yes' END AS nullable,
       CASE c.pk        WHEN 0 THEN ''   ELSE 'pk'  END AS key_role
FROM sqlite_master m
JOIN pragma_table_info(m.name) c
WHERE m.type = 'table'
  AND m.name NOT LIKE 'sqlite_%'
ORDER BY m.name, c.cid;
