from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"  # override with MySQL in .env
    host_total_cpu: int = 8
    host_total_ram_mb: int = 16384
    host_total_storage_mb: int = 102400


settings = Settings()
