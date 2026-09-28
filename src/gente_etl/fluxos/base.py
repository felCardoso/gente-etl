"""Classe base e registro dos fluxos.

Um fluxo = uma base de origem (quadro, admitidos, ...). O ciclo é sempre:

    extrair()        -> lê XLSX (com cache) e aplica as tratativas comuns (padronizar)
    transformar()    -> regras de negócio específicas do fluxo  <- ONDE A LÓGICA DO M ENTRA
    validar()        -> regras genéricas (obrigatórias, chave, datas) + específicas
    validar_final()  -> regras que dependem de outros fluxos (roda depois de todos)

Para criar um fluxo novo basta uma subclasse decorada com ``@registrar``.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.config import Config, ConfigFluxo
from gente_etl.io.excel import ResultadoLeitura, ler_fonte
from gente_etl.validacao import regras
from gente_etl.validacao.resultado import Resultado

REGISTRO: dict[str, type[Fluxo]] = {}

#: Coluna interna com o rótulo da coluna BASE de cada linha. Só é necessária quando um
#: fluxo gera mais de um rótulo (ex.: terceiros); nos demais vale ``Config.rotulo``.
COLUNA_ROTULO = "_rotulo_base"

#: Marca as linhas que, por regra, não têm foto do quadro (ex.: admitido e demitido
#: no mesmo mês). Elas não contam na cobertura do merge.
COLUNA_SEM_QUADRO = "_sem_quadro_esperado"


def registrar(cls: type[Fluxo]) -> type[Fluxo]:
    if cls.nome in REGISTRO:
        raise ValueError(f"Fluxo duplicado: {cls.nome}")
    REGISTRO[cls.nome] = cls
    return cls


@dataclass
class Contexto:
    """Estado compartilhado de uma execução."""

    config: Config
    referencia: date
    usar_cache: bool = True
    resultados: dict[str, pl.DataFrame] = field(default_factory=dict)
    ao_ler: Callable[[ResultadoLeitura], None] | None = None

    @property
    def esquema(self):
        return self.config.esquema_carregado

    @property
    def mes_referencia(self) -> date:
        return self.referencia.replace(day=1)


class Fluxo(ABC):
    nome: ClassVar[str]
    titulo: ClassVar[str]
    frequencia: ClassVar[str] = ""
    depende_de: ClassVar[tuple[str, ...]] = ()
    #: colunas de data checadas por ``datas_plausiveis``
    colunas_data: ClassVar[tuple[str, ...]] = ()

    def __init__(self, ctx: Contexto) -> None:
        self.ctx = ctx
        self.cfg: ConfigFluxo = ctx.config.fluxo(self.nome)
        self._bruto: pl.LazyFrame | None = None

    # ------------------------------------------------------------------ extrair
    def ler(self) -> pl.LazyFrame:
        cfg = self.ctx.config
        return ler_fonte(
            self.cfg,
            base=cfg.raiz,
            pasta_cache=cfg.geral.pasta_cache,
            usar_cache=self.ctx.usar_cache,
            paralelo=cfg.geral.leitura_paralela,
            ao_ler=self.ctx.ao_ler,
            preparar=lambda df: lp.renomear_por_aliases(df, self.ctx.esquema),
        )

    def extrair(self) -> pl.LazyFrame:
        esq = self.ctx.esquema
        bruto = self.ler().pipe(lp.limpar_textos)
        self._bruto = bruto  # guardado para checar falhas de conversão
        return bruto.pipe(lp.remover_linhas_vazias).pipe(lp.converter_tipos, esq)

    # -------------------------------------------------------------- transformar
    def transformar(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """Regras de negócio do fluxo. Sobrescreva nas subclasses."""
        return lf

    @staticmethod
    def definir_chave_m(lf: pl.LazyFrame) -> pl.LazyFrame:
        """Cria ``chave_m`` (``dd/MM/yyyy_matricula``) a partir de ``periodo`` e ``matricula``."""
        return lf.with_columns(lp.chave_m(pl.col("periodo"), pl.col("matricula")).alias("chave_m"))

    def enriquecer_com_quadro(self, lf: pl.LazyFrame) -> pl.LazyFrame:
        """Completa colunas vazias/ausentes com o quadro (padrão: pela CHAVE M, ou seja,
        a foto do quadro do mesmo mês do evento)."""
        e = self.cfg.enriquecer
        quadro = self.ctx.resultados.get("quadro")
        if not e.ativo or quadro is None:
            return lf
        return lp.completar_com_referencia(
            lf,
            quadro.lazy(),
            chave=e.chave,
            chave_referencia=e.chave_quadro or None,
            colunas=e.colunas or None,
            ignorar=e.ignorar,
        )

    # ------------------------------------------------------------------ rótulos
    def rotulos(self) -> list[str]:
        """Valores que este fluxo gera na coluna BASE."""
        return [self.ctx.config.rotulo(self.nome)]

    # ------------------------------------------------------------------ validar
    def validar(self, df: pl.DataFrame) -> list[Resultado]:
        v = self.ctx.config.validacao
        res = regras.sem_linhas(df, self.nome)
        res += regras.colunas_obrigatorias(df, self.cfg.obrigatorias, self.nome, v.linhas_amostra)
        res += regras.chave_unica(df, self.cfg.chave, self.nome, v.linhas_amostra)
        res += regras.periodo_inicio_mes(df, self.nome)
        res += regras.datas_plausiveis(
            df, list(self.colunas_data), self.nome, self.ctx.referencia, n_amostra=v.linhas_amostra
        )
        if self._bruto is not None:
            res += regras.falhas_conversao(self._bruto, self.ctx.esquema, self.nome)
        return res

    def sem_quadro_esperado(self, df: pl.DataFrame) -> pl.Series | None:
        """Linhas que, por regra, não estão em nenhuma foto do quadro. Sobrescreva."""
        return None

    def evento_no_mesmo_mes(
        self, df: pl.DataFrame, outro_fluxo: str, coluna_data: str
    ) -> pl.Series:
        """True quando a pessoa tem o outro evento (admissão/demissão) no mesmo mês.

        Olha a própria coluna de data (se a base tiver) e o resultado do outro fluxo
        pela matrícula + período (se ele rodou nesta execução).
        """
        marca = pl.Series("_mesmo_mes", [False] * df.height)
        if coluna_data in df.columns:
            marca = (
                marca
                | df.select(
                    (lp.inicio_mes(pl.col(coluna_data)) == pl.col("periodo")).fill_null(False)
                ).to_series()
            )
        outro = self.ctx.resultados.get(outro_fluxo)
        chave = ["matricula", "periodo"]
        if outro is not None and set(chave) <= set(outro.columns) and set(chave) <= set(df.columns):
            eventos = outro.select(chave).unique().with_columns(pl.lit(True).alias("_evento"))
            marca = marca | (
                df.select(chave)
                .join(eventos, on=chave, how="left", maintain_order="left")["_evento"]
                .fill_null(False)
            )
        return marca

    def validar_final(self, df: pl.DataFrame) -> list[Resultado]:
        """Validações que dependem dos outros fluxos (``ctx.resultados`` já completo)."""
        if not self.cfg.enriquecer.ativo:
            return []
        esperado = self.sem_quadro_esperado(df)
        if esperado is not None:
            df = df.with_columns(esperado.fill_null(False).alias(COLUNA_SEM_QUADRO))
        return regras.cobertura_enriquecimento(
            df,
            self.nome,
            self.ctx.config.validacao.cobertura_minima_enriquecimento,
            ignorar=COLUNA_SEM_QUADRO if esperado is not None else None,
        )

    # ----------------------------------------------------------------- executar
    def executar(self) -> pl.DataFrame:
        lf = self.extrair().pipe(self.transformar)
        return lf.collect()
