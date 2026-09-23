"""Gravação da base consolidada (Parquet e CSV) de forma atômica.

Grava primeiro num arquivo temporário e só então substitui o definitivo: se algo
falhar no meio, o arquivo que o dataflow lê continua íntegro.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from gente_etl.config import Config


def preparar_para_saida(df: pl.DataFrame, cfg: Config) -> pl.DataFrame:
    """Remove colunas internas (``_*``) e aplica os nomes de saída do esquema."""
    mapa = cfg.esq.mapa_saida()
    publicas = [c for c in df.columns if not c.startswith("_")]
    return df.select(publicas).rename({c: mapa[c] for c in publicas if c in mapa})


def _atomico(destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino.with_name(f".{destino.name}.tmp")


def gravar_parquet(df: pl.DataFrame, destino: Path) -> Path:
    tmp = _atomico(destino)
    df.write_parquet(tmp, compression="zstd", statistics=True)
    tmp.replace(destino)
    return destino


def gravar_csv(df: pl.DataFrame, destino: Path, cfg: Config) -> Path:
    c = cfg.csv
    tmp = _atomico(destino)
    df.write_csv(
        tmp,
        separator=c.separador,
        decimal_comma=c.decimal_virgula,
        include_bom=c.bom,
        date_format=c.formato_data,
        null_value="",
    )
    tmp.replace(destino)
    return destino


def gravar(df: pl.DataFrame, nome: str, cfg: Config) -> list[Path]:
    """Grava ``df`` (já preparado) em todos os formatos configurados."""
    pasta = cfg.geral.pasta_saida
    gravados: list[Path] = []
    for formato in cfg.geral.formatos:
        destino = pasta / f"{nome}.{formato}"
        if formato == "parquet":
            gravados.append(gravar_parquet(df, destino))
        else:
            gravados.append(gravar_csv(df, destino, cfg))
    return gravados
