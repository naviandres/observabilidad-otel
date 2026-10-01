import os
from sqlalchemy import create_engine

# Lee la variable de entorno o usa el valor por defecto apuntando al contenedor postgres
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:admin@postgres:5432/inventory"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)