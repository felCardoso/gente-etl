"""Quadro de colaboradores (headcount). Atualização semanal.

É a base de referência: admitidos, demitidos e movimentações são completados com ela.
"""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar


@registrar
class Quadro(Fluxo):
    nome = "quadro"
    titulo = "Quadro"
    frequencia = "semanal"
    colunas_data = ("data_admissao", "data_nascimento")

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        # 1) Período: usa a coluna da origem, se existir; senão, o mês de referência da execução.
        lf = definir_periodo_foto(lf, self.ctx.mes_referencia)

        # 2) Colunas calculadas --------------------------------------------------------
        # >>> PONTO DE MIGRAÇÃO: traga aqui as colunas personalizadas do dataflow do quadro.
        # Exemplo: tempo de empresa em meses no fim do período.
        lf = lf.with_columns(
            lp.meses_entre(pl.col("data_admissao"), pl.col("periodo").dt.month_end()).alias(
                "tempo_empresa_meses"
            )
        )

        # 3) Duplicadas na chave viram ERRO na validação. Só descarte automaticamente
        #    (mantendo a do arquivo mais recente) se `parametros.remover_duplicadas = true`.
        if self.cfg.parametros.get("remover_duplicadas", False) and self.cfg.chave:
            lf = lp.ultimo_por_chave(lf, self.cfg.chave, ordenar_por=("_arquivo",))
        return lf


def definir_periodo_foto(lf: pl.LazyFrame, mes_referencia) -> pl.LazyFrame:
    """Bases do tipo "foto" (quadro, terceiros): período = coluna da origem ou mês de referência."""
    padrao = pl.lit(mes_referencia, dtype=pl.Date)
    if "periodo" in lf.collect_schema().names():
        return lf.with_columns(lp.inicio_mes(pl.col("periodo")).fill_null(padrao).alias("periodo"))
    return lf.with_columns(padrao.alias("periodo"))
