import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from order_extractor.control import META

url = os.environ["OE_DATABASE"]
if context.is_offline_mode():
    context.configure(url=url, target_metadata=META, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=META)
        with context.begin_transaction():
            context.run_migrations()
