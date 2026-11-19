from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable constraint and index names keep migrations and constraint tests
# independent of SQLAlchemy's generated defaults.
NAMING_CONVENTION = {
    # Index names include the table because Postgres index names are unique per
    # schema, not per table. Expression indexes are named explicitly in the
    # same pattern in models.py.
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    # Check names are written in full at the definition so migrations and models
    # agree even if a naming convention is not applied by the migration context.
    "ck": "%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
