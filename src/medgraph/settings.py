"""Runtime settings, read from environment variables (prefix ``MEDGRAPH_``) and ``.env``."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Paths and runtime options.

    ``data_dir`` holds everything that must never be committed: generated cohorts, downloaded
    tools, run outputs and, later, credentialed datasets. Keep it outside the repository.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDGRAPH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")

    @field_validator("data_dir")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def synthea_dir(self) -> Path:
        return self.data_dir / "synthea"

    @property
    def tools_dir(self) -> Path:
        return self.data_dir / "tools"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"
