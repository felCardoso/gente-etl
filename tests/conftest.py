"""Fixtures compartilhadas. Todos os dados são fictícios (gente_etl.demo)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gente_etl.config import Config, Esquema, carregar_config, carregar_esquema
from gente_etl.demo import criar_ambiente_demo

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def esquema() -> Esquema:
    return carregar_esquema(RAIZ / "src/gente_etl/modelos/esquema.toml")


@pytest.fixture(scope="session")
def _demo_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Gera os XLSX uma única vez por sessão (é a parte lenta)."""
    pasta = tmp_path_factory.mktemp("demo_base")
    criar_ambiente_demo(pasta, colaboradores=150, meses=3)
    return pasta


@pytest.fixture
def demo(_demo_base: Path, tmp_path: Path) -> Path:
    """Cópia isolada do ambiente demo (cada teste pode alterar à vontade)."""
    destino = tmp_path / "demo"
    shutil.copytree(_demo_base, destino)
    return destino


@pytest.fixture
def cfg(demo: Path) -> Config:
    return carregar_config(demo / "config.toml")
