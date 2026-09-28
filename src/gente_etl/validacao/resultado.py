"""Estrutura comum dos resultados de validação."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import polars as pl


class Severidade(IntEnum):
    OK = 0
    INFO = 1
    AVISO = 2
    ERRO = 3  # bloqueia a gravação (a menos que --forcar)

    @property
    def rotulo(self) -> str:
        return {0: "OK", 1: "INFO", 2: "AVISO", 3: "ERRO"}[self.value]


@dataclass
class Resultado:
    regra: str
    escopo: str  # nome do fluxo ou "consolidado"
    severidade: Severidade
    mensagem: str
    ocorrencias: int = 0
    amostra: pl.DataFrame | None = field(default=None, repr=False)

    def como_dict(self) -> dict:
        return {
            "regra": self.regra,
            "escopo": self.escopo,
            "severidade": self.severidade.rotulo,
            "mensagem": self.mensagem,
            "ocorrencias": self.ocorrencias,
        }


def ok(regra: str, escopo: str, mensagem: str) -> Resultado:
    return Resultado(regra, escopo, Severidade.OK, mensagem)


def pior(resultados: list[Resultado]) -> Severidade:
    return max((r.severidade for r in resultados), default=Severidade.OK)
