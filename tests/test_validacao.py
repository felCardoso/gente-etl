from datetime import date

import polars as pl

from gente_etl.validacao import regras
from gente_etl.validacao.resultado import Severidade


def _sev(res):
    return [r.severidade for r in res]


def test_chave_unica_detecta_duplicadas():
    df = pl.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
    (r,) = regras.chave_unica(df, ["a", "b"], "t")
    assert r.severidade == Severidade.ERRO and r.ocorrencias == 2 and r.amostra.height == 2
    assert _sev(regras.chave_unica(df, ["a", "b"][1:] + ["a"], "t")) == [Severidade.ERRO]
    assert _sev(regras.chave_unica(df.unique(), ["a"], "t")) == [Severidade.OK]


def test_obrigatorias_nula_e_ausente():
    df = pl.DataFrame({"a": [1, None]})
    res = regras.colunas_obrigatorias(df, ["a", "b"], "t")
    assert {r.regra for r in res} == {"obrigatoria_nula", "coluna_ausente"}
    assert all(r.severidade == Severidade.ERRO for r in res)


def test_falhas_conversao(esquema):
    bruto = pl.LazyFrame({"data_admissao": ["01/01/2020", "31/02/2020", None]})
    (r,) = regras.falhas_conversao(bruto, esquema, "t")
    assert r.ocorrencias == 1 and "31/02/2020" in r.mensagem


def test_datas_plausiveis():
    df = pl.DataFrame({"d": [date(1900, 1, 1), date(2026, 1, 1), date(2030, 1, 1)]})
    (r,) = regras.datas_plausiveis(df, ["d"], "t", referencia=date(2026, 1, 1))
    assert r.ocorrencias == 2


def _quadro(periodos: dict[date, int]) -> pl.DataFrame:
    return pl.concat(
        [
            pl.DataFrame({"periodo": [p] * n, "matricula": [str(i) for i in range(n)]})
            for p, n in periodos.items()
        ]
    )


def test_reconciliacao_fecha():
    q = _quadro({date(2026, 1, 1): 100, date(2026, 2, 1): 103})
    adm = pl.DataFrame({"periodo": [date(2026, 2, 1)] * 5, "matricula": list("abcde")})
    dem = pl.DataFrame({"periodo": [date(2026, 2, 1)] * 2, "matricula": list("xy")})
    assert _sev(regras.reconciliacao_quadro(q, adm, dem, 0)) == [Severidade.OK]


def test_reconciliacao_nao_fecha_e_nao_estoura_com_mais_demissoes():
    q = _quadro({date(2026, 1, 1): 3, date(2026, 2, 1): 3})
    dem = pl.DataFrame({"periodo": [date(2026, 2, 1)] * 5, "matricula": list("abcde")})
    (r,) = regras.reconciliacao_quadro(q, None, dem, 0.01)
    assert r.severidade == Severidade.AVISO
    assert r.amostra["esperado"].to_list() == [-2]


def test_reconciliacao_pulada_com_um_periodo():
    q = _quadro({date(2026, 1, 1): 3})
    assert _sev(regras.reconciliacao_quadro(q, None, None, 0)) == [Severidade.INFO]


def test_periodos_orcado():
    ok = pl.DataFrame(
        {"periodo": pl.date_range(date(2026, 1, 1), date(2026, 12, 1), "1mo", eager=True)}
    )
    assert _sev(regras.periodos_orcado(ok)) == [Severidade.OK]
    ruim = pl.DataFrame({"periodo": [date(2026, 1, 1), date(2026, 2, 1)]})
    assert {r.regra for r in regras.periodos_orcado(ruim)} == {"orcado_meses"}


def test_periodo_inicio_mes():
    assert regras.periodo_inicio_mes(pl.DataFrame({"periodo": [date(2026, 5, 1)]}), "q") == []
    (r,) = regras.periodo_inicio_mes(pl.DataFrame({"periodo": [date(2026, 5, 31)]}), "q")
    assert r.severidade == Severidade.ERRO and r.regra == "periodo_nao_inicio_mes"


def test_reconciliacao_desconsidera_situacoes_fora_do_hc():
    # fev: 100 ativos + 2 demitidos no mês ainda na foto com situação "D"
    q = pl.concat(
        [
            _quadro({date(2026, 1, 1): 100}).with_columns(pl.lit("A").alias("situacao")),
            pl.DataFrame(
                {
                    "periodo": [date(2026, 2, 1)] * 102,
                    "matricula": [str(i) for i in range(102)],
                    "situacao": ["A"] * 100 + ["D"] * 2,
                }
            ),
        ]
    )
    adm = pl.DataFrame({"periodo": [date(2026, 2, 1)] * 2, "matricula": ["x", "y"]})
    dem = pl.DataFrame({"periodo": [date(2026, 2, 1)] * 2, "matricula": ["100", "101"]})
    assert _sev(regras.reconciliacao_quadro(q, adm, dem, 0)) == [Severidade.AVISO]
    assert _sev(regras.reconciliacao_quadro(q, adm, dem, 0, ["D"])) == [Severidade.OK]


def test_variacao_volume():
    assert _sev(regras.variacao_volume({"Q": 100}, None, 0.2)) == [Severidade.INFO]
    assert _sev(regras.variacao_volume({"Q": 110}, {"Q": 100}, 0.2)) == [Severidade.OK]
    (r,) = regras.variacao_volume({"Q": 50}, {"Q": 100}, 0.2)
    assert r.severidade == Severidade.AVISO and "-50" in r.mensagem


def test_contrato_saida(esquema):
    from gente_etl.comum.limpeza import garantir_colunas

    df = garantir_colunas(pl.LazyFrame({"matricula": ["1"]}), esquema).collect()
    assert _sev(
        regras.contrato_saida(df.with_columns(pl.lit("Q").alias("base")), esquema, "base")
    ) == [Severidade.OK]
    ruim = df.drop("nome").with_columns(pl.lit(1).alias("extra"))
    (r,) = regras.contrato_saida(ruim, esquema, "base")
    assert r.severidade == Severidade.ERRO and "nome" in r.mensagem and "extra" in r.mensagem
