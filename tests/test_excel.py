import os
from datetime import date

import polars as pl

from gente_etl.config import ConfigFluxo
from gente_etl.io.excel import (
    Planilha,
    ler_fonte,
    ler_planilha,
    planilhas_da_fonte,
    resolver_arquivos,
)


def _xlsx(caminho, df, aba="Base"):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    df.write_excel(caminho, worksheet=aba)
    return caminho


def test_resolver_arquivos_ignora_temporarios_do_excel(tmp_path):
    _xlsx(tmp_path / "a.xlsx", pl.DataFrame({"x": [1]}))
    (tmp_path / "~$a.xlsx").write_bytes(b"lock")
    assert [p.name for p in resolver_arquivos(["*.xlsx"], tmp_path)] == ["a.xlsx"]


def test_le_tudo_como_texto(tmp_path):
    arq = _xlsx(tmp_path / "a.xlsx", pl.DataFrame({"n": [1, 2], "d": [date(2026, 1, 1), None]}))
    df = ler_planilha(Planilha(arq, 0), None).df
    assert df.schema == pl.Schema({"n": pl.String, "d": pl.String})


def test_cache_e_reaproveitado_e_invalidado_quando_arquivo_muda(tmp_path):
    arq = _xlsx(tmp_path / "a.xlsx", pl.DataFrame({"x": [1]}))
    cache = tmp_path / "cache"
    p = Planilha(arq, 0)
    assert not ler_planilha(p, cache).do_cache
    assert ler_planilha(p, cache).do_cache
    assert not ler_planilha(p, cache, usar_cache=False).do_cache

    _xlsx(arq, pl.DataFrame({"x": [1, 2]}))
    st = arq.stat()
    os.utime(arq, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    r = ler_planilha(p, cache)
    assert not r.do_cache and r.df.height == 2


def test_todas_as_abas(tmp_path):
    import xlsxwriter

    arq = tmp_path / "o.xlsx"
    with xlsxwriter.Workbook(arq) as wb:
        pl.DataFrame({"x": [1]}).write_excel(wb, worksheet="A")
        pl.DataFrame({"x": [2, 3]}).write_excel(wb, worksheet="B")
    fonte = ConfigFluxo(arquivos=["o.xlsx"], aba="*")
    assert [p.aba for p in planilhas_da_fonte(fonte, tmp_path)] == ["A", "B"]
    df = ler_fonte(fonte, tmp_path, None).collect()
    assert df.height == 3 and set(df["_aba"]) == {"A", "B"}


def test_empilha_arquivos_com_cabecalhos_diferentes_via_preparar(tmp_path, esquema):
    from gente_etl.comum.limpeza import renomear_por_aliases

    _xlsx(tmp_path / "1.xlsx", pl.DataFrame({"CHAPA": ["1"], "NOME": ["A"]}))
    _xlsx(tmp_path / "2.xlsx", pl.DataFrame({"Nome ": ["B"], "Chapa": ["2"]}))
    df = ler_fonte(
        ConfigFluxo(arquivos=["*.xlsx"]),
        tmp_path,
        None,
        preparar=lambda d: renomear_por_aliases(d, esquema),
    ).collect()
    assert sorted(df["matricula"].to_list()) == ["1", "2"]
    assert set(df.columns) == {"matricula", "nome", "_arquivo", "_aba"}
