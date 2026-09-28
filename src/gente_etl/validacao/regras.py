"""Regras de validação automáticas.

Cada regra devolve um ou mais ``Resultado``. Regras de ERRO bloqueiam a gravação
(configurável em ``[validacao] bloquear_em_erro``); AVISO e INFO só aparecem no relatório.

Para criar uma regra nova: escreva uma função que recebe ``pl.DataFrame`` (e o que
mais precisar) e retorna ``list[Resultado]``; chame-a em ``Fluxo.validar`` ou em
``validar_consolidado``.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from gente_etl.comum.limpeza import DTYPES, converter_coluna
from gente_etl.config import Esquema
from gente_etl.validacao.resultado import Resultado, Severidade, ok


def _amostra(df: pl.DataFrame, n: int) -> pl.DataFrame:
    return df.head(n)


def sem_linhas(df: pl.DataFrame, escopo: str) -> list[Resultado]:
    if df.height == 0:
        return [Resultado("sem_linhas", escopo, Severidade.ERRO, "Nenhuma linha após tratamento.")]
    return [ok("sem_linhas", escopo, f"{df.height:,} linhas".replace(",", "."))]


def colunas_obrigatorias(
    df: pl.DataFrame, colunas: list[str], escopo: str, n_amostra: int = 50
) -> list[Resultado]:
    res: list[Resultado] = []
    for c in colunas:
        if c not in df.columns:
            res.append(
                Resultado(
                    "coluna_ausente",
                    escopo,
                    Severidade.ERRO,
                    f"Coluna obrigatória '{c}' não encontrada na origem. "
                    "Confira os aliases em esquema.toml (use `gente-etl inspecionar`).",
                )
            )
            continue
        nulos = df.filter(pl.col(c).is_null())
        if nulos.height:
            res.append(
                Resultado(
                    "obrigatoria_nula",
                    escopo,
                    Severidade.ERRO,
                    f"'{c}' vazia em {nulos.height} linha(s).",
                    nulos.height,
                    _amostra(nulos, n_amostra),
                )
            )
    if not res and colunas:
        res.append(ok("obrigatorias", escopo, f"{len(colunas)} coluna(s) obrigatória(s) completas"))
    return res


def chave_unica(
    df: pl.DataFrame, chave: list[str], escopo: str, n_amostra: int = 50
) -> list[Resultado]:
    if not chave or any(c not in df.columns for c in chave):
        return []
    dup = df.filter(pl.struct(chave).is_duplicated()).sort(chave)
    if dup.height:
        grupos = dup.select(chave).unique().height
        return [
            Resultado(
                "chave_duplicada",
                escopo,
                Severidade.ERRO,
                f"{grupos} chave(s) duplicada(s) em {chave} ({dup.height} linhas).",
                dup.height,
                _amostra(dup, n_amostra),
            )
        ]
    return [ok("chave_unica", escopo, f"Chave {chave} única")]


def falhas_conversao(
    bruto: pl.LazyFrame, esquema: Esquema, escopo: str, n_exemplos: int = 10
) -> list[Resultado]:
    """Valores preenchidos na origem que viraram nulo ao aplicar o tipo (ex.: data inválida)."""
    schema = bruto.collect_schema()
    por_nome = esquema.por_nome
    alvo = [
        n
        for n, dt in schema.items()
        if n in por_nome and por_nome[n].tipo != "texto" and dt != DTYPES[por_nome[n].tipo]
    ]
    if not alvo:
        return []
    falhas_expr = [
        (
            pl.col(n).is_not_null() & converter_coluna(pl.col(n), por_nome[n], schema[n]).is_null()
        ).alias(n)
        for n in alvo
    ]
    mascara = bruto.select(falhas_expr).collect()
    res: list[Resultado] = []
    valores = bruto.select(alvo).collect()
    for n in alvo:
        qtd = int(mascara[n].sum())
        if qtd:
            exemplos = valores.filter(mascara[n])[n].unique().head(n_exemplos).to_list()
            res.append(
                Resultado(
                    "falha_conversao",
                    escopo,
                    Severidade.AVISO,
                    f"'{n}' ({por_nome[n].tipo}): {qtd} valor(es) não convertido(s). "
                    f"Ex.: {exemplos}",
                    qtd,
                    valores.filter(mascara[n]).select(n).unique().head(n_exemplos),
                )
            )
    return res


def datas_plausiveis(
    df: pl.DataFrame,
    colunas: list[str],
    escopo: str,
    referencia: date,
    minimo: date = date(1950, 1, 1),
    dias_futuro: int = 400,
    n_amostra: int = 50,
) -> list[Resultado]:
    limite = date.fromordinal(referencia.toordinal() + dias_futuro)
    res: list[Resultado] = []
    for c in colunas:
        if c not in df.columns or df.schema[c] != pl.Date:
            continue
        fora = df.filter((pl.col(c) < minimo) | (pl.col(c) > limite))
        if fora.height:
            res.append(
                Resultado(
                    "data_implausivel",
                    escopo,
                    Severidade.AVISO,
                    f"'{c}' fora de {minimo:%d/%m/%Y}–{limite:%d/%m/%Y} em {fora.height} linha(s).",
                    fora.height,
                    _amostra(fora, n_amostra),
                )
            )
    return res


def cobertura_enriquecimento(
    df: pl.DataFrame,
    escopo: str,
    minimo: float,
    marcador: str = "_encontrado_referencia",
    ignorar: str | None = None,
) -> list[Resultado]:
    """% de linhas encontradas no quadro. ``ignorar`` = coluna booleana das linhas que,
    por regra, não têm foto (ex.: admitido e demitido no mesmo mês)."""
    if marcador not in df.columns or df.height == 0:
        return []
    sem_foto = 0
    if ignorar and ignorar in df.columns:
        sem_foto = int(df[ignorar].sum())
        df = df.filter(~pl.col(ignorar))
    obs = f" {sem_foto} linha(s) sem foto por regra, fora da conta." if sem_foto else ""
    if df.height == 0:
        return [
            Resultado("cobertura_quadro", escopo, Severidade.OK, "Nenhuma linha a conferir." + obs)
        ]
    achados = int(df[marcador].sum())
    taxa = achados / df.height
    nao = df.filter(~pl.col(marcador))
    sev = Severidade.OK if taxa >= minimo else Severidade.AVISO
    return [
        Resultado(
            "cobertura_quadro",
            escopo,
            sev,
            f"{taxa:.1%} das linhas encontradas no quadro ({df.height - achados} sem correspondência;"
            f" mínimo {minimo:.0%}).{obs}",
            df.height - achados,
            nao.head(50) if nao.height else None,
        )
    ]


def reconciliacao_quadro(
    quadro: pl.DataFrame,
    admitidos: pl.DataFrame | None,
    demitidos: pl.DataFrame | None,
    tolerancia: float,
    situacoes_fora_hc: list[str] | None = None,
) -> list[Resultado]:
    """Quadro(t) ≈ Quadro(t-1) + Admitidos(t) − Demitidos(t), por período (mês).

    Só roda se o quadro tiver pelo menos dois períodos. Transferências internas não
    alteram o total da companhia, por isso não entram na conta. Linhas do quadro com
    situação em ``situacoes_fora_hc`` (ex.: demitidos que ainda aparecem na foto do
    mês) não contam como HC.
    """
    escopo = "consolidado"
    if situacoes_fora_hc and "situacao" in quadro.columns:
        quadro = quadro.filter(~pl.col("situacao").is_in(situacoes_fora_hc).fill_null(False))
    if "periodo" not in quadro.columns or quadro["periodo"].n_unique() < 2:
        return [
            Resultado(
                "reconciliacao",
                escopo,
                Severidade.INFO,
                "Reconciliação pulada: o quadro tem menos de 2 períodos.",
            )
        ]

    def _contar(df: pl.DataFrame | None, nome: str) -> pl.DataFrame:
        if df is None or "periodo" not in df.columns:
            return pl.DataFrame(schema={"periodo": pl.Date, nome: pl.Int64})
        return df.group_by("periodo").agg(pl.col("matricula").n_unique().cast(pl.Int64).alias(nome))

    hc = _contar(quadro, "hc").sort("periodo")
    tabela = (
        hc.with_columns(pl.col("hc").shift(1).alias("hc_anterior"))
        .join(_contar(admitidos, "admitidos"), on="periodo", how="left")
        .join(_contar(demitidos, "demitidos"), on="periodo", how="left")
        .fill_null(0)
        .filter(pl.col("hc_anterior") > 0)
        .with_columns(
            (pl.col("hc_anterior") + pl.col("admitidos") - pl.col("demitidos")).alias("esperado")
        )
        .with_columns((pl.col("hc") - pl.col("esperado")).alias("diferenca"))
        .with_columns((pl.col("diferenca").abs() / pl.col("hc")).alias("desvio"))
    )
    fora = tabela.filter(pl.col("desvio") > tolerancia)
    if fora.height:
        return [
            Resultado(
                "reconciliacao",
                escopo,
                Severidade.AVISO,
                f"{fora.height} período(s) em que quadro anterior + admitidos − demitidos "
                f"não fecha com o quadro (tolerância {tolerancia:.1%}).",
                fora.height,
                fora,
            )
        ]
    return [ok("reconciliacao", escopo, f"Quadro reconciliado em {tabela.height} período(s)")]


def periodo_inicio_mes(df: pl.DataFrame, escopo: str) -> list[Resultado]:
    """O período é sempre o 1º dia do mês (base da CHAVE M)."""
    if "periodo" not in df.columns or df.schema["periodo"] != pl.Date:
        return []
    nao_dia1 = df.filter(pl.col("periodo").dt.day() != 1)
    if nao_dia1.height:
        return [
            Resultado(
                "periodo_nao_inicio_mes",
                escopo,
                Severidade.ERRO,
                f"{nao_dia1.height} linha(s) com período que não é dia 1.",
                nao_dia1.height,
                nao_dia1.head(50),
            )
        ]
    return []


def periodos_orcado(df: pl.DataFrame, escopo: str = "orcado") -> list[Resultado]:
    if "periodo" not in df.columns or df.height == 0:
        return []
    res: list[Resultado] = []
    meses = df.group_by(pl.col("periodo").dt.year().alias("ano")).agg(
        pl.col("periodo").n_unique().alias("meses")
    )
    incompletos = meses.filter(pl.col("meses") != 12)
    if incompletos.height:
        res.append(
            Resultado(
                "orcado_meses",
                escopo,
                Severidade.AVISO,
                "Ano(s) do orçado sem 12 meses: "
                + ", ".join(
                    f"{r['ano']} ({r['meses']})" for r in incompletos.iter_rows(named=True)
                ),
                incompletos.height,
                incompletos,
            )
        )
    if not res:
        res.append(ok("orcado_periodos", escopo, "Orçado com 12 meses por ano"))
    return res


def variacao_volume(
    atual: dict[str, int], anterior: dict[str, int] | None, limite: float
) -> list[Resultado]:
    if not anterior:
        return [Resultado("variacao_volume", "consolidado", Severidade.INFO, "Primeira execução.")]
    res: list[Resultado] = []
    for base, qtd in atual.items():
        antes = anterior.get(base)
        if not antes:
            continue
        var = (qtd - antes) / antes
        if abs(var) > limite:
            res.append(
                Resultado(
                    "variacao_volume",
                    base,
                    Severidade.AVISO,
                    f"Linhas variaram {var:+.1%} vs. última execução ({antes} → {qtd}).",
                    abs(qtd - antes),
                )
            )
    if not res:
        res.append(ok("variacao_volume", "consolidado", f"Volumes dentro de ±{limite:.0%}"))
    return res


def contrato_saida(df: pl.DataFrame, esquema: Esquema, coluna_base: str) -> list[Resultado]:
    """Confere se a base final tem exatamente as colunas/tipos do esquema (contrato do BI)."""
    esperado = {c.nome: DTYPES[c.tipo] for c in esquema.coluna}
    publicas = [c for c in df.columns if not c.startswith("_") and c != coluna_base]
    problemas = []
    faltando = [c for c in esperado if c not in publicas]
    extras = [c for c in publicas if c not in esperado]
    tipos = [c for c in publicas if c in esperado and df.schema[c] != esperado[c]]
    if faltando:
        problemas.append(f"faltando {faltando}")
    if extras:
        problemas.append(f"fora do esquema {extras}")
    if tipos:
        problemas.append("tipo divergente " + ", ".join(f"{c}={df.schema[c]}" for c in tipos))
    if problemas:
        return [
            Resultado(
                "contrato_saida",
                "consolidado",
                Severidade.ERRO,
                "Base final: " + "; ".join(problemas),
            )
        ]
    return [ok("contrato_saida", "consolidado", f"{len(esperado)} colunas conforme o esquema")]
