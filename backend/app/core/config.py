from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://root:@127.0.0.1:3306/cloudsched"  # MySQL is required for the DBMS features
    host_total_cpu: int = 8
    host_total_ram_mb: int = 16384
    host_total_storage_mb: int = 102400

    # --- execution + automation -------------------------------------------------------------
    executor_mode: str = "simulate"        # "simulate" (no Docker needed) | "docker" (real containers)
    time_scale: float = 1.0                # real seconds per simulation time unit (est_time * time_scale)
    docker_image: str = "python:3.12-alpine"
    automation_enabled: bool = False       # start the background loop with the API
    automation_interval: float = 2.0       # seconds between cycles
    automation_policy: str = "PRIORITY_AGING"   # FCFS | SJF | PRIORITY | PRIORITY_AGING | NONE
    aging_seconds: int = 60


settings = Settings()
