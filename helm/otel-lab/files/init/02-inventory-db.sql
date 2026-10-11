-- Creates the inventory database (service-b) and seeds it. Runs on a fresh data
-- directory after 01-orders-schema.sql. Idempotent: CREATE DATABASE has no
-- IF NOT EXISTS, so it is guarded with \gexec; the seed uses ON CONFLICT.
SELECT 'CREATE DATABASE inventory WITH OWNER = postgres ENCODING = ''UTF8'''
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'inventory')\gexec

\connect inventory

CREATE TABLE IF NOT EXISTS inventory (
    sku      VARCHAR(50) PRIMARY KEY,
    quantity INTEGER NOT NULL
);

INSERT INTO inventory (sku, quantity)
VALUES ('LAPTOP-001', 10),
       ('MOUSE-001', 20),
       ('KEYBOARD-001', 15)
ON CONFLICT (sku) DO NOTHING;
