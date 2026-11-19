-- Ruolo di sola lettura per gli agenti hlab sul database CRM (Neon). DA ESEGUIRE A MANO da Lorenzo, come proprietario, nella SQL Editor di Neon.
-- Oggi gli agenti leggono con le credenziali del proprietario (`neondb_owner`): il codice non scrive, ma un bug potrebbe. Questo ruolo lo rende impossibile
-- anche dal lato database: vede solo le 6 tabelle che gli strumenti usano (niente users, sessions, audit, token) e non puo' modificare nulla.
--
-- 1) Sostituisci SOSTITUISCI_CON_PASSWORD con una password lunga e casuale (dal tuo gestore di password), poi esegui tutto.
-- 2) Sul server, in ~/hermes_lab/.env aggiungi CHF_PG_USER=hlab_readonly e CHF_PG_PASSWORD=<la stessa password>, poi `sudo systemctl restart hlab-router hlab-worker`.
--    (il bot Hermes continua a usare la sua password in ~/hermes.env: non lo tocca)
-- 3) Verifica con `hlab ask "scadenze 14 giorni"` e con `hlab cred check` a monte: deve rispondere come prima.

CREATE ROLE hlab_readonly LOGIN PASSWORD 'SOSTITUISCI_CON_PASSWORD';
ALTER ROLE hlab_readonly SET default_transaction_read_only = on;     -- anche se qualcuno aggiungesse un permesso per errore, le transazioni restano di sola lettura
ALTER ROLE hlab_readonly CONNECTION LIMIT 5;
ALTER ROLE hlab_readonly SET statement_timeout = '15s';

GRANT CONNECT ON DATABASE agency_ai TO hlab_readonly;
GRANT USAGE ON SCHEMA public TO hlab_readonly;
GRANT SELECT ON projects, clients, tasks, payments, leads, documents TO hlab_readonly;

-- AGGIUNTA 2026-11-06 (per il riepilogo delle fatture nei conti dell'azienda): eseguire anche questa riga, sempre come proprietario.
GRANT SELECT ON invoices TO hlab_readonly;

-- Controllo (devono dare: 6 righe SELECT e nessuna tabella sensibile)
-- SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee = 'hlab_readonly' ORDER BY 1;

-- Per annullare tutto:
-- REVOKE ALL ON projects, clients, tasks, payments, leads, documents FROM hlab_readonly;
-- REVOKE ALL ON SCHEMA public FROM hlab_readonly; REVOKE CONNECT ON DATABASE agency_ai FROM hlab_readonly; DROP ROLE hlab_readonly;
