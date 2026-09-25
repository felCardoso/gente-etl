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
        # Período = mês do evento; CHAVE M liga à foto do quadro desse mesmo mês.
        lf = lf.with_columns(lp.inicio_mes(pl.col("data_admissao")).alias("periodo")).pipe(
            self.definir_chave_m
        )

        # Completa colunas ausentes/vazias com o quadro do mês (merge pela CHAVE M).
        lf = self.enriquecer_com_quadro(lf)

        # >>> PONTO DE MIGRAÇÃO: colunas calculadas / filtros específicos de admitidos.
        return lf

    def sem_quadro_esperado(self, df: pl.DataFrame) -> pl.Series:
        # Admitido e demitido no mesmo mês não aparece na foto: fica só com os dados
        # da própria base e não conta como falha de merge.
        return self.evento_no_mesmo_mes(df, "demitidos", "data_demissao")
