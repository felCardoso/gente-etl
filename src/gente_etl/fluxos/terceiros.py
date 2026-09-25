"""Terceiros (prestadores). Uma base só, separada em três rótulos na coluna BASE:

    QUADRO-TERCEIROS · ADMITIDOS-TERCEIROS · DEMITIDOS-TERCEIROS

Não é completada pelo quadro. A regra que separa os três rótulos vem do dataflow
atual (ver ``classificar``); enquanto ela não for migrada, a configuração aceita um
de-para simples: uma coluna da planilha e o rótulo de cada valor.
"""

from __future__ import annotations

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.fluxos.base import COLUNA_ROTULO, Fluxo, registrar
from gente_etl.fluxos.quadro import definir_periodo_foto
from gente_etl.io.excel import ler_fonte
from gente_etl.validacao import regras
from gente_etl.validacao.resultado import Resultado, Severidade

QUADRO = "QUADRO-TERCEIROS"
ADMITIDOS = "ADMITIDOS-TERCEIROS"
DEMITIDOS = "DEMITIDOS-TERCEIROS"
ROTULOS = (QUADRO, ADMITIDOS, DEMITIDOS)

#: Coluna interna com o valor usado na classificação (não vai para a saída).
COLUNA_CLASSIFICACAO = "_classificacao"

#: Chave de unicidade por rótulo. Sem matrícula, os demitidos são identificados
#: por período + nome. Rótulos fora daqui usam ``chave`` do config.
CHAVES_PADRAO: dict[str, list[str]] = {DEMITIDOS: ["periodo", "nome"]}


@registrar
class Terceiros(Fluxo):
    nome = "terceiros"
    titulo = "Terceiros"
    frequencia = "semanal"
    colunas_data = ("data_admissao", "data_demissao")

    def rotulos(self) -> list[str]:
        return list(ROTULOS)

    def ler(self) -> pl.LazyFrame:
        # A coluna que classifica não está no esquema (não vai para a saída): é
        # guardada como coluna interna antes de descartar as colunas desconhecidas.
        cfg = self.ctx.config
        alvo = lp.normalizar_nome(self.cfg.parametros.get("coluna_classificacao") or "")

        def preparar(df: pl.DataFrame) -> pl.DataFrame:
            origem = next((c for c in df.columns if alvo and lp.normalizar_nome(c) == alvo), None)
            if origem is not None:
                df = df.with_columns(pl.col(origem).alias(COLUNA_CLASSIFICACAO))
            return lp.renomear_por_aliases(df, self.ctx.esquema)

        return ler_fonte(
            self.cfg,
            base=cfg.raiz,
            pasta_cache=cfg.geral.pasta_cache,
            usar_cache=self.ctx.usar_cache,
            paralelo=cfg.geral.leitura_paralela,
            ao_ler=self.ctx.ao_ler,
            preparar=preparar,
        )

    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        lf = definir_periodo_foto(lf, self.ctx.mes_referencia)  # sem período: mês de referência
        lf = self.classificar(lf)

        # >>> PONTO DE MIGRAÇÃO: demais regras específicas de terceiros.
        if self.cfg.parametros.get("remover_duplicadas", False) and self.cfg.chave:
            lf = lp.ultimo_por_chave(lf, self.cfg.chave, ordenar_por=("_arquivo",))
        return lf

    def classificar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """Cria ``_rotulo_base`` com QUADRO-, ADMITIDOS- ou DEMITIDOS-TERCEIROS.

        >>> PONTO DE MIGRAÇÃO: a regra oficial está no dataflow de terceiros. Troque o
        de-para abaixo pela regra do M se ela for mais que "valor da coluna X → rótulo".
        Linha sem rótulo vira ERRO na validação (``terceiros_sem_classificacao``).
        """
        mapa = {
            lp.normalizar_nome(str(k)): str(v).upper()
            for k, v in self.cfg.parametros.get("classificacao", {}).items()
        }
        if COLUNA_CLASSIFICACAO not in lf.collect_schema().names() or not mapa:
            return lf.with_columns(pl.lit(None, dtype=pl.String).alias(COLUNA_ROTULO))
        # Mesma normalização dos cabeçalhos: "Admitido " -> "ADMITIDO".
        valor = (
            lp.remover_acentos(pl.col(COLUNA_CLASSIFICACAO).cast(pl.String))
            .str.to_uppercase()
            .str.replace_all(r"[^0-9A-Z]+", "_")
            .str.strip_chars("_")
        )
        return lf.with_columns(
            valor.replace_strict(mapa, default=None, return_dtype=pl.String).alias(COLUNA_ROTULO)
        )

    def chaves(self) -> dict[str, list[str]]:
        extras = self.cfg.parametros.get("chaves", {})
        return {**CHAVES_PADRAO, **{str(k).upper(): list(v) for k, v in extras.items()}}

    def validar(self, df: pl.DataFrame) -> list[Resultado]:
        v = self.ctx.config.validacao
        res = regras.sem_linhas(df, self.nome)
        res += regras.colunas_obrigatorias(df, self.cfg.obrigatorias, self.nome, v.linhas_amostra)
        res += regras.periodo_inicio_mes(df, self.nome)
        res += regras.datas_plausiveis(
            df, list(self.colunas_data), self.nome, self.ctx.referencia, n_amostra=v.linhas_amostra
        )
        if self._bruto is not None:
            res += regras.falhas_conversao(self._bruto, self.ctx.esquema, self.nome)

        rotulo = pl.col(COLUNA_ROTULO)
        sem = df.filter(rotulo.is_null() | ~rotulo.is_in(ROTULOS))
        if sem.height:
            p = self.cfg.parametros
            dica = (
                "Configure parametros.coluna_classificacao e parametros.classificacao"
                if not p.get("coluna_classificacao")
                else "Valor sem rótulo em parametros.classificacao"
            )
            res.append(
                Resultado(
                    "terceiros_sem_classificacao",
                    self.nome,
                    Severidade.ERRO,
                    f"{sem.height} linha(s) sem rótulo QUADRO/ADMITIDOS/DEMITIDOS-TERCEIROS. {dica}"
                    " (ou migre a regra do dataflow em fluxos/terceiros.py: classificar).",
                    sem.height,
                    sem.head(v.linhas_amostra),
                )
            )

        # Chave única por rótulo (ex.: demitidos = período + nome).
        chaves = self.chaves()
        for r in ROTULOS:
            chave = chaves.get(r, self.cfg.chave)
            if chave:
                res += regras.chave_unica(
                    df.filter(rotulo == r), chave, f"{self.nome}/{r}", v.linhas_amostra
                )
        return res
