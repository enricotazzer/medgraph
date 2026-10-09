"""Runtime settings, read from environment variables (prefix ``MEDGRAPH_``) and ``.env``."""

from pathlib import Path
from typing import Self
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class RemoteLLMError(ValueError):
    """An LLM setting would send data off this machine without explicit permission."""


def check_local_llm(base_url: str, model: str, allow_remote: bool) -> None:
    """Refuse an LLM endpoint or model that would process data off this machine.

    Two ways out exist: an endpoint on another host, and Ollama cloud models (tags such as
    ``gemma4:cloud``), which a local Ollama server forwards to ollama.com.
    """
    if allow_remote:
        return
    host = urlparse(base_url).hostname
    if host not in LOCAL_HOSTS:
        raise RemoteLLMError(
            f"LLM endpoint {base_url} is not on this machine; "
            "set MEDGRAPH_ALLOW_REMOTE_LLM=true only if that is intended"
        )
    if "cloud" in model.lower():
        raise RemoteLLMError(
            f"{model} is an Ollama cloud model and runs off this machine; "
            "set MEDGRAPH_ALLOW_REMOTE_LLM=true only if that is intended"
        )


class Settings(BaseSettings):
    """Paths and runtime options.

    ``data_dir`` holds everything that must never be committed: generated cohorts, downloaded
    tools, run outputs and, later, credentialed datasets. Keep it outside the repository.

    The LLM backend is local by default; any remote endpoint or cloud model must be enabled
    explicitly with ``allow_remote_llm``.
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDGRAPH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:9b"
    llm_timeout_s: float = 900
    allow_remote_llm: bool = False

    @field_validator("data_dir")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _llm_stays_local(self) -> Self:
        check_local_llm(self.llm_base_url, self.llm_model, self.allow_remote_llm)
        return self

    @property
    def synthea_dir(self) -> Path:
        return self.data_dir / "synthea"

    @property
    def tools_dir(self) -> Path:
        return self.data_dir / "tools"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def lab_reports_dir(self) -> Path:
        return self.data_dir / "lab_reports"

    @property
    def graphs_dir(self) -> Path:
        return self.data_dir / "graphs"

    @property
    def views_dir(self) -> Path:
        return self.data_dir / "views"

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"
