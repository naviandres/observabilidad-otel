CREATE DATABASE orders
    WITH
    OWNER = usuario
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.UTF-8'
    LC_CTYPE = 'en_US.UTF-8'
    CONNECTION LIMIT = -1;


CREATE TABLE orders (
                        id VARCHAR(50) PRIMARY KEY,
                        total_amount NUMERIC(15,2) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
                             id SERIAL PRIMARY KEY,
                             order_id VARCHAR(50) NOT NULL,
                             sku VARCHAR(50) NOT NULL,
                             quantity INTEGER NOT NULL,
                             unit_price NUMERIC(15,2) NOT NULL
);


CREATE DATABASE inventory
    WITH
    OWNER = usuario
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.UTF-8'
    LC_CTYPE = 'en_US.UTF-8'
    CONNECTION LIMIT = -1;



CREATE TABLE inventory (
                           sku VARCHAR(50) PRIMARY KEY,
                           quantity INTEGER NOT NULL
);

INSERT INTO inventory (sku, quantity)
VALUES
    ('LAPTOP-001', 10),
    ('MOUSE-001', 20),
    ('KEYBOARD-001', 15);