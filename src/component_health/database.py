from sqlalchemy import create_engine
from component_health.settings import Settings


def build_engine(settings:Settings,*,read_only=False):
    options={'connect_timeout':3}
    if read_only:options['options']='-c default_transaction_read_only=on'
    return create_engine(settings.database_url.get_secret_value(),pool_pre_ping=True,pool_size=2,max_overflow=1,pool_timeout=2,connect_args=options,echo=False)
