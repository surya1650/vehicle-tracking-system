import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    load_dotenv()
    value = os.environ.get("DATABASE_URL", "")
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("Set DATABASE_URL to a postgresql+psycopg connection string.")
    return value


def make_sessions() -> sessionmaker[Session]:
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    return sessionmaker(engine, expire_on_commit=False)
