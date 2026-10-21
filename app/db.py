import time
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().sqlalchemy_url()
        kwargs = {"pool_pre_ping": True}
        if str(url).startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False}}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401 - registers tables

    # Several workers start at once; if two create the same table, the loser retries
    # and finds the tables already there.
    for attempt in range(3):
        try:
            Base.metadata.create_all(get_engine())
            _add_missing_columns()
            return
        except Exception:  # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def get_db() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Additive migrations: new model columns are added to existing tables (as NULL-able)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                try:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type} NULL"))
                except Exception:  # noqa: BLE001 - another worker added it first
                    pass
