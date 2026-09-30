from sqlalchemy import create_engine


DATABASE_URL = (
    "postgresql+psycopg2://"
    "postgres:admin@localhost:5432/inventory"
)


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)