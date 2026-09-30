CREATE DATABASE inventory WITH OWNER = postgres ENCODING = 'UTF8';


CREATE TABLE orders (
                        id VARCHAR(50) PRIMARY KEY,
                        total_amount NUMERIC(15,2) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
                             id SERIAL PRIMARY KEY,
                             order_id VARCHAR(50) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                             sku VARCHAR(50) NOT NULL,
                             quantity INTEGER NOT NULL,
                             unit_price NUMERIC(15,2) NOT NULL
);