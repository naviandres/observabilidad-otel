CREATE DATABASE inventory WITH OWNER = postgres ENCODING = 'UTF8';


CREATE TABLE inventory (
                           sku VARCHAR(50) PRIMARY KEY,
                           quantity INTEGER NOT NULL
);

INSERT INTO inventory (sku, quantity)
VALUES
    ('LAPTOP-001', 10),
    ('MOUSE-001', 20),
    ('KEYBOARD-001', 15);