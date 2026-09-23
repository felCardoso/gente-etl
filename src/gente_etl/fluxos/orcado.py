"""Orçado (quadro orçado). Atualização semestral.

Uma linha = um HC orçado, com campos parecidos com os do quadro, para o ano inteiro,
com ``periodo`` no primeiro dia de cada mês.
"""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar
from gente_etl.validacao import regras
from gente_etl.validacao.resultado import Resultado


@registrar
class Orcado(Fluxo):
    nome = "orcado"
    titulo = "Orçado"
    frequencia = "semestral"
    colunas_data = ("periodo",)

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # Por padrão NÃO corrige o período: um período fora do dia 1 é erro de origem
        # e aparece na validação. Ligue `parametros.normalizar_periodo = true` para corrigir.
        if self.cfg.parametros.get("normalizar_periodo", False):
            lf = lf.with_columns(lp.inicio_mes(pl.col("periodo")).alias("periodo"))

        # >>> PONTO DE MIGRAÇÃO: regras específicas do orçado.
        return lf

    def validar(self, df: pl.DataFrame) -> list[Resultado]:
        return super().validar(df) + regras.periodos_orcado(df, self.nome)
