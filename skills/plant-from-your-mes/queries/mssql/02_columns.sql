-- Step 2 — the columns, their declared types, and which are keys.
-- Reads: the catalogue only. Still no plant data.
-- Why: this is the step where you work out which table is the machine list,
--      which is the tag history, and which is somebody's spreadsheet import.
-- Read-only. One row per column.
SELECT c.TABLE_SCHEMA                                AS schema_name,
       c.TABLE_NAME                                  AS table_name,
       c.COLUMN_NAME                                 AS column_name,
       c.DATA_TYPE                                   AS data_type,
       c.IS_NULLABLE                                 AS nullable,
       COALESCE(k.key_role, '')                      AS key_role
FROM INFORMATION_SCHEMA.COLUMNS c
LEFT JOIN (
    SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME,
           MIN(CASE tc.CONSTRAINT_TYPE WHEN 'PRIMARY KEY' THEN 'pk' ELSE 'fk' END) AS key_role
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
    JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
      ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
     AND tc.CONSTRAINT_SCHEMA = ku.CONSTRAINT_SCHEMA
    GROUP BY ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
) k ON k.TABLE_SCHEMA = c.TABLE_SCHEMA
   AND k.TABLE_NAME = c.TABLE_NAME
   AND k.COLUMN_NAME = c.COLUMN_NAME
WHERE c.TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA')
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION;
