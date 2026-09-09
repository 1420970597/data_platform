"""应用配置。"""
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """从环境变量读取运行配置。"""
    database_url: str = "sqlite:///./military_platform.db"
    app_env: str = "development"
    secret_key: str = "change-me"
    data_dir: str = "./data"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
