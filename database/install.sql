-- CloudSched full MySQL DBMS installation. Run from this directory using mysql client:
--   mysql -u root -p < install.sql
-- WARNING: 01_schema.sql resets CloudSched tables and deletes existing data.
SOURCE 01_schema.sql;
SOURCE 02_views.sql;
SOURCE 03_procedures.sql;
SOURCE 04_triggers.sql;
SOURCE 05_seed_data.sql;
