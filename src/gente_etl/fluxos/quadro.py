"""Quadro de colaboradores (headcount). Foto do fechamento de cada mês.

É a base de referência: admitidos, demitidos e movimentações são completados com ela
pela CHAVE M (``dd/MM/yyyy_matricula`` do período).

Os arquivos podem vir empilhados de várias formas e ser misturados na mesma pasta:
um ano ("Quadro 2020"), vários anos ("Quadro 2018-2019") ou um mês ("Quadro 05-2026").
"""

from __future__ import annotations

from datetime import date

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import Fluxo, registrar
from gente_etl.io.excel import COLUNA_ARQUIVO, COLUNA_ORDEM
from gente_etl.validacao.resultado import Resultado, Severidade


@registrar
class Quadro(Fluxo):
    nome = "quadro"
    titulo = "Quadro"
    frequencia = "semanal"
    colunas_data = ("data_admissao", "data_nascimento")

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        p = self.cfg.parametros
        # 1) Período (sempre dia 1): coluna da planilha; senão, "MM-AAAA" do nome do arquivo.
        #    Mês de referência da execução só se `periodo_padrao_referencia = true`.
        padrao = self.ctx.mes_referencia if p.get("periodo_padrao_referencia", False) else None
        lf = definir_periodo_foto(lf, padrao)

        # 2) Mesmo mês em mais de um arquivo (ex.: "Quadro 2026" e "Quadro 05-2026"):
        #    vale o arquivo modificado mais recentemente. `sobreposicao = "erro"` desliga.
        self._origem_periodos = lf.select("periodo", COLUNA_ARQUIVO, COLUNA_ORDEM)
        if p.get("sobreposicao", "mais_recente") == "mais_recente":
            lf = lf.filter(pl.col(COLUNA_ORDEM) == pl.col(COLUNA_ORDEM).max().over("periodo"))

        # 3) CHAVE M
        lf = lf.with_columns(lp.chave_m(pl.col("periodo"), pl.col("matricula")).alias("chave_m"))

        # 4) Colunas calculadas --------------------------------------------------------
        # >>> PONTO DE MIGRAÇÃO: traga aqui as colunas personalizadas do dataflow do quadro.
        # Exemplo: tempo de empresa em meses no fim do período.
        lf = lf.with_columns(
            lp.meses_entre(pl.col("data_admissao"), pl.col("periodo").dt.month_end()).alias(
                "tempo_empresa_meses"
            )
        )

        # 5) Duplicadas na chave viram ERRO na validação. Só descarte automaticamente
        #    (mantendo a do arquivo mais recente) se `parametros.remover_duplicadas = true`.
        if p.get("remover_duplicadas", False) and self.cfg.chave:
            lf = lp.ultimo_por_chave(lf, self.cfg.chave, ordenar_por=(COLUNA_ORDEM,))
        return lf

    def executar(self) -> pl.DataFrame:
        # Base e resumo de sobreposição num único collect: o Polars reaproveita a leitura
        # e a conversão de tipos em vez de refazê-las para a validação.
        lf = self.extrair().pipe(self.transformar)
        df, self._repetidos = pl.collect_all([lf, self._periodos_repetidos()])
        return df

    def _periodos_repetidos(self) -> pl.LazyFrame:
        return (
            self._origem_periodos.group_by("periodo")
            .agg(
                pl.col(COLUNA_ARQUIVO).unique().sort().alias("arquivos"),
                pl.col(COLUNA_ARQUIVO).sort_by(COLUNA_ORDEM).last().alias("usado"),
            )
            .filter(pl.col("arquivos").list.len() > 1)
            .sort("periodo")
        )

    def validar(self, df: pl.DataFrame) -> list[Resultado]:
        return super().validar(df) + self._validar_sobreposicao()

    def _validar_sobreposicao(self) -> list[Resultado]:
        repetidos = getattr(self, "_repetidos", None)
        if repetidos is None and hasattr(self, "_origem_periodos"):
            repetidos = self._periodos_repetidos().collect()
        if repetidos is None or repetidos.height == 0:
            return []
        resolver = self.cfg.parametros.get("sobreposicao", "mais_recente") == "mais_recente"
        exemplos = "; ".join(
            f"{r['periodo']:%m/%Y} em {r['arquivos']}"
            + (f" → usado {r['usado']!r}" if resolver else "")
            for r in repetidos.head(5).iter_rows(named=True)
        )
        return [
            Resultado(
                "periodo_em_varios_arquivos",
                self.nome,
                Severidade.AVISO if resolver else Severidade.ERRO,
                f"{repetidos.height} período(s) em mais de um arquivo"
                + (" (mantido o arquivo mais recente)" if resolver else "")
                + f": {exemplos}",
                repetidos.height,
                repetidos.with_columns(pl.col("arquivos").list.join(" | ")),
            )
        ]


def definir_periodo_foto(lf: pl.LazyFrame, padrao: date | None) -> pl.LazyFrame:
    """Bases do tipo "foto" (quadro, terceiros).

    Período = coluna da planilha → "MM-AAAA" no nome do arquivo → ``padrao`` (se houver).
    O valor da planilha não é corrigido: período fora do dia 1 vira ERRO na validação.
    """
    cols = lf.collect_schema().names()
    opcoes = []
    if "periodo" in cols:
        opcoes.append(pl.col("periodo"))
    if COLUNA_ARQUIVO in cols:
        opcoes.append(lp.periodo_do_nome_arquivo(pl.col(COLUNA_ARQUIVO)))
    if padrao is not None:
        opcoes.append(pl.lit(padrao, dtype=pl.Date))
    if not opcoes:
        return lf.with_columns(pl.lit(None, dtype=pl.Date).alias("periodo"))
    return lf.with_columns(pl.coalesce(opcoes).alias("periodo"))
