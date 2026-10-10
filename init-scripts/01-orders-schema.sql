-- postgres images run /docker-entrypoint-initdb.d/*.sql only on a fresh data
-- directory, against POSTGRES_DB (orders). This mirrors service-a/db.sql and is
-- idempotent so it can be re-applied by hand without failing.
CREATE TABLE IF NOT EXISTS orders (
    id           VARCHAR(50) PRIMARY KEY,
    total_amount NUMERIC(15, 2) NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   VARCHAR(50) NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    sku        VARCHAR(50) NOT NULL,
    quantity   INTEGER NOT NULL,
    unit_price NUMERIC(15, 2) NOT NULL
);
