"""Demitidos. Atualização semanal. Completado com o quadro."""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar


@registrar
class Demitidos(Fluxo):
    nome = "demitidos"
    titulo = "Demitidos"
    frequencia = "semanal"
    depende_de = ("quadro",)
    colunas_data = ("data_admissao", "data_demissao")

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        lf = lf.with_columns(lp.inicio_mes(pl.col("data_demissao")).alias("periodo"))
        lf = self.enriquecer_com_quadro(lf)

        # >>> PONTO DE MIGRAÇÃO: colunas calculadas / filtros específicos de demitidos.
        # Exemplo: tempo de empresa na data do desligamento (usa data_admissao vinda do quadro).
        lf = lf.with_columns(
            lp.meses_entre(pl.col("data_admissao"), pl.col("data_demissao")).alias(
                "tempo_empresa_meses"
            )
        )
        return lf
