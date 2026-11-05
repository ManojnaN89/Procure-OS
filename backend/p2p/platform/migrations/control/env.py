from alembic import context
from sqlalchemy import create_engine

# The URL is passed in code (Config.attributes["url"]) so no password sits in a file.
url: str = context.config.attributes["url"]
engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
