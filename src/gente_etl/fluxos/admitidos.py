"""Admitidos. Atualização semanal. Completado com o quadro."""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar


@registrar
class Admitidos(Fluxo):
    nome = "admitidos"
    titulo = "Admitidos"
    frequencia = "semanal"
    depende_de = ("quadro",)
    colunas_data = ("data_admissao",)

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # Período do evento = mês da admissão.
        lf = lf.with_columns(lp.inicio_mes(pl.col("data_admissao")).alias("periodo"))

        # Completa colunas ausentes/vazias com o quadro (merge).
        lf = self.enriquecer_com_quadro(lf)

        # >>> PONTO DE MIGRAÇÃO: colunas calculadas / filtros específicos de admitidos.
        return lf
