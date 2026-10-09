-- Script d'initialisation PostgreSQL pour l'application Gestion Chair
-- À exécuter avec l'utilisateur superadmin postgres (ex: psql -U postgres -f init_postgres.sql)

DO
$do$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'poulailler') THEN
      CREATE ROLE poulailler WITH LOGIN PASSWORD 'poulailler_local';
   END IF;
END
$do$;

ALTER ROLE poulailler WITH CREATEDB;

SELECT 'CREATE DATABASE poulailler OWNER poulailler'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'poulailler')\gexec

GRANT ALL PRIVILEGES ON DATABASE poulailler TO poulailler;
