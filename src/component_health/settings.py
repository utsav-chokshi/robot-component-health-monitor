from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings,SettingsConfigDict


class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_prefix='RCHM_',extra='ignore')
    database_url:SecretStr
    raw_directory:Path=Path('/raw')
    analytical_directory:Path=Path('/analytics')
