from datetime import date

import polars as pl
import pytest

from gente_etl.comum import limpeza as lp


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Data de Admissão ", "DATA_DE_ADMISSAO"),
        ("CHAPA", "CHAPA"),
        (" nome  do colaborador", "NOME_DO_COLABORADOR"),
        ("Cód. Seção", "COD_SECAO"),
    ],
)
def test_normalizar_nome(entrada, esperado):
    assert lp.normalizar_nome(entrada) == esperado


def test_para_data_aceita_varios_formatos():
    s = pl.Series(
        "d",
        [
            "2026-01-05 00:00:00",
            "2026-01-05",
            "05/01/2026",
            "05-01-2026",
            "46027",
            "46027.0",
            "20260105",
            "31/02/2026",
            "lixo",
            None,
        ],
    )
    r = pl.select(lp.para_data(pl.lit(s))).to_series().to_list()
    assert r[:7] == [date(2026, 1, 5)] * 7
    assert r[7:] == [None, None, None]


def test_para_decimal_formatos_brasileiros():
    s = pl.Series(["1.234,56", "1234.56", "R$ 10,5", "12%", "", None, "abc"])
    r = pl.select(lp.para_decimal(pl.lit(s))).to_series().to_list()
    assert r == [1234.56, 1234.56, 10.5, 12.0, None, None, None]


def test_para_booleano():
    s = pl.Series(["Sim", "NÃO", "s", "n", "1", "0", "talvez", None])
    r = pl.select(lp.para_booleano(pl.lit(s))).to_series().to_list()
    assert r == [True, False, True, False, True, False, None, None]


def test_limpar_textos_trim_e_vazio_vira_nulo():
    lf = pl.LazyFrame({"a": ["  x  y ", "", "   "], "n": [1, 2, 3]})
    r = lf.pipe(lp.limpar_textos).collect()
    assert r["a"].to_list() == ["x y", None, None]
    assert r["n"].to_list() == [1, 2, 3]


def test_renomear_por_aliases_ignora_acento_caixa_e_descarta_extras(esquema):
    df = pl.DataFrame({"Chapa": ["1"], " Data de Admissão": ["x"], "Lixo": [1], "_arquivo": ["a"]})
    r = lp.renomear_por_aliases(df, esquema)
    assert r.columns == ["matricula", "data_admissao", "_arquivo"]


def test_renomear_por_aliases_primeira_ocorrencia_vence(esquema):
    df = pl.DataFrame({"CHAPA": ["1"], "MATRICULA": ["2"]})
    assert lp.renomear_por_aliases(df, esquema).to_dict(as_series=False) == {"matricula": ["1"]}


def test_converter_tipos_com_zeros_esquerda(esquema):
    lf = pl.LazyFrame(
        {
            "cpf": ["123", "12345678901"],
            "data_admissao": ["01/02/2020", None],
            "tempo_empresa_meses": ["12", "1.234,0"],
        }
    )
    r = lp.converter_tipos(lf, esquema).collect()
    assert r["cpf"].to_list() == ["00000000123", "12345678901"]
    assert r["data_admissao"].to_list() == [date(2020, 2, 1), None]
    assert r["tempo_empresa_meses"].to_list() == [12, 1234]
    assert r.schema["tempo_empresa_meses"] == pl.Int64


def test_garantir_colunas_ordena_e_cria_faltantes(esquema):
    lf = pl.LazyFrame({"nome": ["a"], "_arquivo": ["x"], "matricula": ["1"]})
    r = lp.garantir_colunas(lf, esquema).collect()
    assert r.columns == [*esquema.nomes, "_arquivo"]
    assert r.schema["data_admissao"] == pl.Date
    assert (
        lp.garantir_colunas(lf, esquema, manter_internas=False).collect().columns == esquema.nomes
    )


@pytest.mark.parametrize(
    ("ini", "fim", "meses"),
    [
        (date(2020, 1, 15), date(2020, 2, 14), 0),
        (date(2020, 1, 15), date(2020, 2, 15), 1),
        (date(2020, 1, 31), date(2021, 1, 31), 12),
        (date(2019, 12, 1), date(2020, 1, 1), 1),
    ],
)
def test_meses_entre(ini, fim, meses):
    r = pl.select(lp.meses_entre(pl.lit(ini), pl.lit(fim))).item()
    assert r == meses


def test_completar_com_referencia_prioriza_valor_proprio_e_usa_mais_recente():
    ref = pl.LazyFrame(
        {
            "matricula": ["1", "1", "2"],
            "periodo": [date(2026, 1, 1), date(2026, 2, 1), date(2026, 2, 1)],
            "cc": ["ANTIGO", "NOVO", "CC2"],
            "gestor": ["G1", "G1b", "G2"],
        }
    )
    lf = pl.LazyFrame({"matricula": ["1", "2", "3"], "cc": [None, "PROPRIO", None]})
    r = lp.completar_com_referencia(lf, ref, chave=["matricula"], ignorar=["periodo"]).collect()
    assert r["cc"].to_list() == ["NOVO", "PROPRIO", None]  # mais recente / próprio / sem par
    assert r["gestor"].to_list() == ["G1b", "G2", None]  # coluna inexistente é criada
    assert r["_encontrado_referencia"].to_list() == [True, True, False]
    assert "periodo" not in r.columns
    assert r.height == 3  # left join não multiplica linhas


def test_completar_com_referencia_colunas_especificas():
    ref = pl.LazyFrame({"matricula": ["1"], "a": ["x"], "b": ["y"]})
    lf = pl.LazyFrame({"matricula": ["1"]})
    r = lp.completar_com_referencia(lf, ref, chave=["matricula"], colunas=["a"], marcar=None)
    assert r.collect().columns == ["matricula", "a"]


def test_remover_linhas_vazias_ignora_colunas_internas():
    lf = pl.LazyFrame({"a": [None, "x"], "b": [None, None], "_arquivo": ["f", "f"]})
    assert lp.remover_linhas_vazias(lf).collect().height == 1
