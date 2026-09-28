"""Movimentações (transferências, promoções, mudanças de cargo...). Atualização mensal."""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar


@registrar
class Movimentacoes(Fluxo):
    nome = "movimentacoes"
    titulo = "Movimentações"
    frequencia = "mensal"
    depende_de = ("quadro",)
    colunas_data = ("data_movimentacao",)

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # Período = mês do evento; CHAVE M liga à foto do quadro desse mesmo mês.
        lf = lf.with_columns(lp.inicio_mes(pl.col("data_movimentacao")).alias("periodo")).pipe(
            self.definir_chave_m
        )
        lf = self.enriquecer_com_quadro(lf)

        # >>> PONTO DE MIGRAÇÃO: regras de movimentação (ex.: classificar tipo, de/para).
        return lf
