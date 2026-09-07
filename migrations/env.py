from alembic import context
from app.database import engine, Base
from app import models


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


provided = context.config.attributes.get('connection')
if provided is not None:
    run(provided)
else:
    with engine.connect() as connection:
        run(connection)
