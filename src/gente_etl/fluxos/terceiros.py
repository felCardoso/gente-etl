"""Terceiros (prestadores). Foto por período, sem enriquecimento pelo quadro."""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar
from gente_etl.fluxos.quadro import definir_periodo_foto


@registrar
class Terceiros(Fluxo):
    nome = "terceiros"
    titulo = "Terceiros"
    frequencia = "semanal"
    colunas_data = ("data_admissao",)

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        lf = definir_periodo_foto(lf, self.ctx.mes_referencia)

        # >>> PONTO DE MIGRAÇÃO: regras específicas de terceiros.
        if self.cfg.parametros.get("remover_duplicadas", False) and self.cfg.chave:
            lf = lp.ultimo_por_chave(lf, self.cfg.chave, ordenar_por=("_arquivo",))
        return lf
