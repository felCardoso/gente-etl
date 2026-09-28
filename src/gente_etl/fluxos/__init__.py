"""Fluxos disponíveis. Importar este pacote registra todos eles em ``REGISTRO``."""

from gente_etl.fluxos import (  # noqa: F401  (importados para registrar)
    admitidos,
    demitidos,
    movimentacoes,
    orcado,
    quadro,
    terceiros,
)
from gente_etl.fluxos.base import COLUNA_ROTULO, REGISTRO, Contexto, Fluxo, registrar

__all__ = ["COLUNA_ROTULO", "REGISTRO", "Contexto", "Fluxo", "registrar"]
