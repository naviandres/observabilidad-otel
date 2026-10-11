#!/bin/bash
set -e

# 1. Crear base de datos inventory y sus tablas
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE inventory WITH OWNER = postgres ENCODING = 'UTF8';
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "inventory" <<-EOSQL
    CREATE TABLE IF NOT EXISTS inventory (
        sku VARCHAR(50) PRIMARY KEY,
        quantity INTEGER NOT NULL
    );

    INSERT INTO inventory (sku, quantity)
    VALUES
        ('LAPTOP-001', 10),
        ('MOUSE-001', 20),
        ('KEYBOARD-001', 15);
EOSQL

# 2. Crear tablas en la base de datos orders (base de datos por defecto)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "orders" <<-EOSQL
    CREATE TABLE IF NOT EXISTS orders (
        id VARCHAR(50) PRIMARY KEY,
        total_amount NUMERIC(15,2) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id SERIAL PRIMARY KEY,
        order_id VARCHAR(50) NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        sku VARCHAR(50) NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price NUMERIC(15,2) NOT NULL
    );
EOSQL