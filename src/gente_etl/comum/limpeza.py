"""Tratativas comuns a todos os fluxos.

Todas as funções recebem/retornam ``pl.LazyFrame`` ou ``pl.Expr`` e são vetorizadas
(nada de ``map_elements``/loops por linha) - é daqui que vem a performance.

Equivalências com Power Query (M) estão comentadas para facilitar a migração.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import TypeVar

import polars as pl

from gente_etl.config import Coluna, Esquema

# Formatos de data aceitos, em ordem de tentativa.
FORMATOS_DATA = (
    "%Y-%m-%d %H:%M:%S",  # datas reais do Excel lidas como texto pelo calamine
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y",
    "%Y%m%d",
)
_EPOCA_EXCEL = pl.date(1899, 12, 30)

Tabela = TypeVar("Tabela", pl.DataFrame, pl.LazyFrame)

VERDADEIROS = ("SIM", "S", "TRUE", "VERDADEIRO", "1", "X", "Y", "YES")
FALSOS = ("NAO", "N", "FALSE", "FALSO", "0", "NO")  # comparado já sem acento


# --------------------------------------------------------------------------------------
# Nomes de colunas
# --------------------------------------------------------------------------------------
def normalizar_nome(texto: str) -> str:
    """'Data de Admissão ' -> 'DATA_DE_ADMISSAO'. Usado para casar cabeçalhos com aliases."""
    sem_acento = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9A-Z]+", "_", sem_acento.upper()).strip("_")


def mapa_aliases(esquema: Esquema) -> dict[str, str]:
    """Cabeçalho normalizado -> nome interno. Inclui o próprio nome e o nome de saída."""
    mapa: dict[str, str] = {}
    for c in esquema.coluna:
        for alias in (c.nome, c.nome_saida, *c.aliases):
            mapa.setdefault(normalizar_nome(alias), c.nome)
    return mapa


def renomear_por_aliases(lf: Tabela, esquema: Esquema, manter_extras: bool = False) -> Tabela:
    """Renomeia cabeçalhos da origem para os nomes internos do esquema.

    Colunas sem correspondência são descartadas (a menos que ``manter_extras``).
    Colunas internas (prefixo ``_``) são sempre mantidas.
    Equivalente M: ``Table.RenameColumns`` + ``Table.SelectColumns``.
    """
    mapa = mapa_aliases(esquema)
    renomear: dict[str, str] = {}
    usados: set[str] = set()
    for col in lf.collect_schema().names():
        if col.startswith("_"):
            continue
        destino = mapa.get(normalizar_nome(col))
        if destino and destino not in usados:
            renomear[col] = destino
            usados.add(destino)
    selecionar = [
        c
        for c in lf.collect_schema().names()
        if c in renomear or c.startswith("_") or manter_extras
    ]
    return lf.select(selecionar).rename(renomear)


# --------------------------------------------------------------------------------------
# Texto
# --------------------------------------------------------------------------------------
def limpar_texto(expr: pl.Expr) -> pl.Expr:
    """Trim + espaços múltiplos -> um + vazio vira nulo. (M: Text.Trim / Text.Clean)"""
    limpo = expr.str.strip_chars().str.replace_all(r"\s+", " ")
    return pl.when(limpo == "").then(None).otherwise(limpo)


def limpar_textos(lf: pl.LazyFrame) -> pl.LazyFrame:
    textos = [n for n, t in lf.collect_schema().items() if t == pl.String]
    return lf.with_columns([limpar_texto(pl.col(c)).alias(c) for c in textos])


def remover_acentos(expr: pl.Expr) -> pl.Expr:
    """Remove acentos de forma vetorizada (sem Python por linha)."""
    de = "ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑáàâãäéèêëíìîïóòôõöúùûüçñ"
    para = "AAAAAEEEEIIIIOOOOOUUUUCNaaaaaeeeeiiiiooooouuuucn"
    for a, b in zip(de, para, strict=True):
        expr = expr.str.replace_all(a, b, literal=True)
    return expr


def remover_linhas_vazias(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Remove linhas em que todas as colunas de dados são nulas. (M: Table.SelectRows(... <> null))"""
    dados = [c for c in lf.collect_schema().names() if not c.startswith("_")]
    if not dados:
        return lf
    return lf.filter(~pl.all_horizontal(pl.col(dados).is_null()))


# --------------------------------------------------------------------------------------
# Conversões robustas (origem lida como texto)
# --------------------------------------------------------------------------------------
def para_data(expr: pl.Expr) -> pl.Expr:
    """Converte texto em data aceitando vários formatos e número serial do Excel.

    M: ``Date.From`` / ``Table.TransformColumnTypes(..., type date)``.
    """
    texto = expr.cast(pl.String).str.strip_chars()
    tentativas = [texto.str.strptime(pl.Date, f, strict=False, exact=True) for f in FORMATOS_DATA]
    serial = texto.str.extract(r"^(\d{4,6})(?:\.0+)?$", 1).cast(pl.Int32, strict=False)
    tentativas.append(
        pl.when(serial.is_between(1, 2_958_465))  # até 31/12/9999
        .then(_EPOCA_EXCEL + pl.duration(days=serial))
        .otherwise(None)
    )
    return pl.coalesce(tentativas)


def para_decimal(expr: pl.Expr) -> pl.Expr:
    """'R$ 1.234,56' / '1234.56' / '12%' -> Float64. Vírgula é tratada como decimal."""
    texto = expr.cast(pl.String).str.strip_chars().str.replace_all(r"[R$\s%]", "")
    com_virgula = texto.str.contains(",", literal=True)
    normal = (
        pl.when(com_virgula)
        .then(texto.str.replace_all(".", "", literal=True).str.replace(",", ".", literal=True))
        .otherwise(texto)
    )
    return normal.cast(pl.Float64, strict=False)


def para_inteiro(expr: pl.Expr) -> pl.Expr:
    return para_decimal(expr).round(0).cast(pl.Int64, strict=False)


def para_booleano(expr: pl.Expr) -> pl.Expr:
    texto = remover_acentos(expr.cast(pl.String).str.strip_chars().str.to_uppercase())
    return (
        pl.when(texto.is_in(VERDADEIROS))
        .then(True)
        .when(texto.is_in(FALSOS))
        .then(False)
        .otherwise(None)
    )


def converter_coluna(expr: pl.Expr, coluna: Coluna, dtype_atual: pl.DataType) -> pl.Expr:
    tipo = coluna.tipo
    if tipo == "texto":
        e = expr.cast(pl.String)
        if dtype_atual.is_float():  # 123.0 -> "123"
            e = expr.cast(pl.Int64, strict=False).cast(pl.String)
        e = limpar_texto(e)
        if coluna.maiusculas:
            e = e.str.to_uppercase()
        if coluna.zeros_esquerda:
            e = e.str.zfill(coluna.zeros_esquerda)
        return e
    if tipo == "data":
        if dtype_atual == pl.Date:
            return expr
        if isinstance(dtype_atual, pl.Datetime):
            return expr.dt.date()
        return para_data(expr)
    if tipo == "decimal":
        return expr.cast(pl.Float64) if dtype_atual.is_numeric() else para_decimal(expr)
    if tipo == "inteiro":
        if dtype_atual.is_integer():
            return expr.cast(pl.Int64)
        return para_inteiro(expr)
    if tipo == "booleano":
        return expr if dtype_atual == pl.Boolean else para_booleano(expr)
    raise ValueError(f"Tipo desconhecido: {tipo}")  # pragma: no cover


DTYPES: dict[str, pl.DataType] = {
    "texto": pl.String(),
    "inteiro": pl.Int64(),
    "decimal": pl.Float64(),
    "data": pl.Date(),
    "booleano": pl.Boolean(),
}


def converter_tipos(lf: pl.LazyFrame, esquema: Esquema) -> pl.LazyFrame:
    """Aplica o tipo do esquema em cada coluna presente. (M: Table.TransformColumnTypes)"""
    schema = lf.collect_schema()
    por_nome = esquema.por_nome
    exprs = [
        converter_coluna(pl.col(nome), por_nome[nome], dtype).alias(nome)
        for nome, dtype in schema.items()
        if nome in por_nome
    ]
    return lf.with_columns(exprs) if exprs else lf


def garantir_colunas(
    lf: pl.LazyFrame, esquema: Esquema, manter_internas: bool = True
) -> pl.LazyFrame:
    """Cria colunas ausentes (nulas, já tipadas) e ordena conforme o esquema."""
    existentes = set(lf.collect_schema().names())
    faltantes = [
        pl.lit(None, dtype=DTYPES[c.tipo]).alias(c.nome)
        for c in esquema.coluna
        if c.nome not in existentes
    ]
    if faltantes:
        lf = lf.with_columns(faltantes)
    internas = [c for c in existentes if c.startswith("_")] if manter_internas else []
    return lf.select([*esquema.nomes, *sorted(internas)])


# --------------------------------------------------------------------------------------
# Datas / períodos
# --------------------------------------------------------------------------------------
def inicio_mes(expr: pl.Expr) -> pl.Expr:
    """M: Date.StartOfMonth."""
    return expr.dt.month_start()


def chave_m(periodo: pl.Expr, matricula: pl.Expr) -> pl.Expr:
    """CHAVE M = ``dd/MM/yyyy_matricula`` do período (sempre dia 1). Ex.: ``01/05/2026_000123``.

    Liga cada linha à foto do quadro do mesmo mês. M: ``Date.ToText([periodo], "dd/MM/yyyy")
    & "_" & [matricula]``.
    """
    return pl.concat_str([periodo.dt.strftime("%d/%m/%Y"), matricula], separator="_")


def periodo_do_nome_arquivo(arquivo: pl.Expr) -> pl.Expr:
    """Extrai ``MM-AAAA`` do nome do arquivo ("Quadro 05-2026.xlsx" -> 01/05/2026).

    Nomes de ano ("Quadro 2020") ou de faixa ("Quadro 2018-2019") não casam e dão nulo.
    """
    partes = arquivo.str.extract_groups(r"(?:^|\D)(?<mes>\d{2})-(?<ano>\d{4})(?:\D|$)")
    mes = partes.struct.field("mes").cast(pl.Int32, strict=False)
    ano = partes.struct.field("ano").cast(pl.Int32, strict=False)
    return pl.when(mes.is_between(1, 12)).then(pl.date(ano, mes.clip(1, 12), 1)).otherwise(None)


def meses_entre(inicio: pl.Expr, fim: pl.Expr) -> pl.Expr:
    """Meses completos entre duas datas (equivalente a DATEDIF 'M')."""
    meses = (fim.dt.year() - inicio.dt.year()) * 12 + (fim.dt.month() - inicio.dt.month())
    return (meses - (fim.dt.day() < inicio.dt.day()).cast(pl.Int32)).cast(pl.Int64)


# --------------------------------------------------------------------------------------
# Deduplicação e enriquecimento (merges)
# --------------------------------------------------------------------------------------
def ultimo_por_chave(
    lf: pl.LazyFrame, chave: list[str], ordenar_por: Iterable[str] = ()
) -> pl.LazyFrame:
    """Mantém a linha mais recente por chave. (M: Table.Sort + Table.Distinct)"""
    ordem = [c for c in ordenar_por if c in lf.collect_schema().names()]
    if ordem:
        lf = lf.sort(ordem, descending=True, nulls_last=True)
    return lf.unique(subset=chave, keep="first", maintain_order=True)


def completar_com_referencia(
    lf: pl.LazyFrame,
    referencia: pl.LazyFrame,
    chave: list[str],
    colunas: list[str] | None = None,
    ignorar: Iterable[str] = (),
    ordenar_referencia_por: Iterable[str] = ("periodo",),
    marcar: str | None = "_encontrado_referencia",
) -> pl.LazyFrame:
    """Preenche colunas ausentes ou em branco a partir de uma base de referência.

    É o "quadro popula admitidos/demitidos": para cada linha, o valor próprio tem
    prioridade e o valor da referência (registro mais recente por chave) entra só
    quando o próprio é nulo ou a coluna nem existe.

    M: ``Table.NestedJoin`` (LeftOuter) + ``Table.ExpandTableColumn`` + coluna
    condicional ``if [x] = null then [quadro.x] else [x]`` - aqui feito num único join.
    """
    cols_ref = referencia.collect_schema().names()
    cols_lf = set(lf.collect_schema().names())
    ignorar = set(ignorar) | set(chave)
    alvo = [
        c
        for c in (colunas or cols_ref)
        if c in cols_ref and c not in ignorar and not c.startswith("_")
    ]
    if not alvo:
        return lf

    ref = ultimo_por_chave(referencia, chave, ordenar_referencia_por).select(
        [*chave, *[pl.col(c).alias(f"__ref_{c}") for c in alvo]]
    )
    if marcar:
        ref = ref.with_columns(pl.lit(True).alias(f"__ref_{marcar}"))

    juntado = lf.join(ref, on=chave, how="left", coalesce=True)
    exprs = []
    for c in alvo:
        if c in cols_lf:
            exprs.append(pl.coalesce(pl.col(c), pl.col(f"__ref_{c}")).alias(c))
        else:
            exprs.append(pl.col(f"__ref_{c}").alias(c))
    if marcar:
        exprs.append(pl.col(f"__ref_{marcar}").fill_null(False).alias(marcar))
    juntado = juntado.with_columns(exprs)
    return juntado.drop([c for c in juntado.collect_schema().names() if c.startswith("__ref_")])


def padronizar(lf: pl.LazyFrame, esquema: Esquema) -> pl.LazyFrame:
    """Pipeline padrão de toda fonte: renomeia, limpa texto, remove vazias e tipa."""
    return (
        lf.pipe(renomear_por_aliases, esquema)
        .pipe(limpar_textos)
        .pipe(remover_linhas_vazias)
        .pipe(converter_tipos, esquema)
    )
