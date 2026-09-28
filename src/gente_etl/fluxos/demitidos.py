"""Demitidos. Atualização semanal.

O demitido sai do quadro no mês da demissão, por isso é completado com a foto do
fechamento do MÊS ANTERIOR, pela CHAVE M-1. Na base consolidada, as linhas de
demitidos ficam com BASE = "DEMITIDOS". Quem foi admitido e demitido no mesmo mês
não está em nenhuma foto e fica só com os dados da própria base.
"""

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
        # Período = mês da demissão. CHAVE M é a do próprio mês; CHAVE M-1 (mês anterior)
        # é a usada no merge com o quadro (config: enriquecer.chave = ["chave_m_1"]).
        lf = lf.with_columns(lp.inicio_mes(pl.col("data_demissao")).alias("periodo")).pipe(
            self.definir_chave_m
        )
        lf = lf.with_columns(
            lp.chave_m_anterior(pl.col("periodo"), pl.col("matricula")).alias("chave_m_1")
        )
        lf = self.enriquecer_com_quadro(lf)

        # >>> PONTO DE MIGRAÇÃO: colunas calculadas / filtros específicos de demitidos.
        # Exemplo: tempo de empresa na data do desligamento (usa data_admissao vinda do quadro).
        lf = lf.with_columns(
            lp.meses_entre(pl.col("data_admissao"), pl.col("data_demissao")).alias(
                "tempo_empresa_meses"
            )
        )
        return lf

    def sem_quadro_esperado(self, df: pl.DataFrame) -> pl.Series:
        # Admitido e demitido no mesmo mês não está na foto do mês anterior: fica só
        # com os dados da própria base e não conta como falha de merge.
        return self.evento_no_mesmo_mes(df, "admitidos", "data_admissao")
