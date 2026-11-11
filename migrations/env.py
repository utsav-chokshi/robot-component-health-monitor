from alembic import context
from component_health.database import build_engine
from component_health.settings import Settings


if context.is_offline_mode():
    raise RuntimeError('Offline migration output is not supported; use the explicit migration service')
engine=build_engine(Settings())
try:
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=None)
        with context.begin_transaction():context.run_migrations()
finally:engine.dispose()
