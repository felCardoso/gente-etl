"""Leitura de XLSX com calamine (fastexcel) e cache em Parquet.

* Tudo é lido como **texto** e tipado depois pelo esquema: evita surpresas com
  colunas de tipo misto (o motivo nº 1 de erro em Power Query).
* Cada (arquivo, aba) lido vira um Parquet em ``pasta_cache``. A chave do cache
  considera caminho, tamanho e data de modificação: se o XLSX não mudou, a leitura
  (a parte mais lenta) é pulada. O orçado, que muda uma vez por semestre, praticamente
  nunca é relido.
* Arquivos são lidos em paralelo (``leitura_paralela``).
"""

from __future__ import annotations

import glob
import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import fastexcel
import polars as pl

from gente_etl.config import ConfigFluxo, expandir_caminho

# Mude quando a forma de leitura mudar, para invalidar caches antigos.
VERSAO_LEITOR = "1"
COLUNA_ARQUIVO = "_arquivo"
COLUNA_ABA = "_aba"


class ErroLeitura(Exception):
    """Falha ao localizar ou ler arquivos de origem."""


@dataclass(frozen=True)
class Planilha:
    arquivo: Path
    aba: str | int
    linha_cabecalho: int = 0


@dataclass
class ResultadoLeitura:
    planilha: Planilha
    df: pl.DataFrame
    do_cache: bool


def resolver_arquivos(padroes: list[str], base: Path) -> list[Path]:
    """Expande padrões glob. Ignora arquivos temporários do Excel (``~$...``)."""
    encontrados: dict[Path, None] = {}
    for padrao in padroes:
        caminho = expandir_caminho(padrao, base)
        for p in sorted(glob.glob(str(caminho), recursive=True)):
            arq = Path(p)
            if arq.is_file() and not arq.name.startswith("~$"):
                encontrados[arq.resolve()] = None
    return list(encontrados)


def listar_abas(arquivo: Path) -> list[str]:
    return fastexcel.read_excel(arquivo).sheet_names


def planilhas_da_fonte(fonte: ConfigFluxo, base: Path) -> list[Planilha]:
    arquivos = resolver_arquivos(fonte.arquivos, base)
    planilhas: list[Planilha] = []
    for arq in arquivos:
        if fonte.aba == "*":
            planilhas += [Planilha(arq, aba, fonte.linha_cabecalho) for aba in listar_abas(arq)]
        else:
            planilhas.append(Planilha(arq, fonte.aba, fonte.linha_cabecalho))
    return planilhas


def chave_cache(p: Planilha) -> str:
    st = p.arquivo.stat()
    bruto = f"{p.arquivo}|{st.st_size}|{st.st_mtime_ns}|{p.aba}|{p.linha_cabecalho}|{VERSAO_LEITOR}"
    return hashlib.sha1(bruto.encode()).hexdigest()[:20]


def ler_planilha_bruta(p: Planilha, n_linhas: int | None = None) -> pl.DataFrame:
    """Lê uma aba inteira como texto, sem cache."""
    try:
        leitor = fastexcel.read_excel(p.arquivo)
        aba = leitor.load_sheet(
            p.aba,
            header_row=p.linha_cabecalho,
            dtypes="string",
            n_rows=n_linhas,
            whitespace_as_null=True,
            skip_whitespace_tail_rows=True,
        )
        return aba.to_polars()
    except Exception as e:  # fastexcel levanta vários tipos; padronizamos a mensagem
        raise ErroLeitura(f"Não foi possível ler {p.arquivo.name} (aba {p.aba!r}): {e}") from e


def ler_planilha(
    p: Planilha, pasta_cache: Path | None, usar_cache: bool = True
) -> ResultadoLeitura:
    cache = None
    if pasta_cache is not None:
        cache = pasta_cache / "xlsx" / f"{p.arquivo.stem}-{chave_cache(p)}.parquet"
        if usar_cache and cache.exists():
            return ResultadoLeitura(p, pl.read_parquet(cache), do_cache=True)

    df = ler_planilha_bruta(p)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        df.write_parquet(tmp, compression="lz4")
        tmp.replace(cache)
    return ResultadoLeitura(p, df, do_cache=False)


def ler_fonte(
    fonte: ConfigFluxo,
    base: Path,
    pasta_cache: Path | None,
    usar_cache: bool = True,
    paralelo: int = 4,
    ao_ler: Callable[[ResultadoLeitura], None] | None = None,
    preparar: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.LazyFrame:
    """Lê e empilha todos os arquivos/abas de uma fonte.

    ``preparar`` é aplicado a cada arquivo ANTES de empilhar (ex.: renomear pelos
    aliases), para que "CHAPA" num arquivo e "Chapa" no outro virem a mesma coluna.
    Adiciona ``_arquivo`` e ``_aba`` para rastreabilidade (removidas na gravação).
    """
    planilhas = planilhas_da_fonte(fonte, base)
    if not planilhas:
        caminhos = [str(expandir_caminho(p, base)) for p in fonte.arquivos]
        dica = ""
        if any("%" in c or "$" in c for c in caminhos):
            dica = "\nHá variável de ambiente não definida no caminho (%...% ou ${...})."
        raise ErroLeitura(
            "Nenhum arquivo encontrado para os padrões:\n"
            + "\n".join(f"  • {c}" for c in caminhos)
            + dica
        )

    def _ler(p: Planilha) -> ResultadoLeitura:
        r = ler_planilha(p, pasta_cache, usar_cache)
        if ao_ler:
            ao_ler(r)
        return r

    with ThreadPoolExecutor(max_workers=min(paralelo, len(planilhas))) as ex:
        resultados = list(ex.map(_ler, planilhas))

    partes = [
        (preparar(r.df) if preparar else r.df).with_columns(
            pl.lit(r.planilha.arquivo.name).alias(COLUNA_ARQUIVO),
            pl.lit(str(r.planilha.aba)).alias(COLUNA_ABA),
        )
        for r in resultados
    ]
    # diagonal: arquivos com colunas diferentes (ou em outra ordem) são alinhados por nome
    return pl.concat(partes, how="diagonal_relaxed").lazy()
