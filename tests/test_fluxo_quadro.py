"""Quadro: período, sobreposição de arquivos e CHAVE M."""

from datetime import date

import polars as pl

from gente_etl.fluxos.base import Contexto
from gente_etl.fluxos.quadro import Quadro
from gente_etl.validacao.resultado import Severidade

REF = date(2026, 8, 1)


def _entrada(**colunas) -> pl.LazyFrame:
    base = {"matricula": ["1"], "data_admissao": [date(2020, 1, 1)]}
    base.update(colunas)
    n = len(next(iter(colunas.values()))) if colunas else 1
    base = {k: (v * n if len(v) == 1 else v) for k, v in base.items()}
    return pl.LazyFrame(base)


def _fluxo(cfg, **parametros) -> Quadro:
    cfg.fluxos["quadro"].parametros.update(parametros)
    return Quadro(Contexto(config=cfg, referencia=REF))


def test_periodo_da_coluna_depois_do_nome_do_arquivo(cfg):
    entrada = _entrada(
        periodo=[date(2020, 3, 1), None, None],
        matricula=["1", "2", "3"],
        _arquivo=["Quadro 2020.xlsx", "Quadro 05-2026.xlsx", "Quadro 2018-2019.xlsx"],
        _ordem_arquivo=["1", "2", "3"],
    )
    r = _fluxo(cfg).transformar(entrada).collect().sort("matricula")
    assert r["periodo"].to_list() == [date(2020, 3, 1), date(2026, 5, 1), None]
    assert r["chave_m"].to_list() == ["01/03/2020_1", "01/05/2026_2", None]


def test_periodo_padrao_referencia_opcional(cfg):
    entrada = _entrada(_arquivo=["Quadro 2020.xlsx"], _ordem_arquivo=["1"])
    r = _fluxo(cfg, periodo_padrao_referencia=True).transformar(entrada).collect()
    assert r["periodo"].to_list() == [date(2026, 8, 1)]


def test_mes_em_dois_arquivos_usa_o_mais_recente_e_avisa(cfg):
    maio = date(2026, 5, 1)
    entrada = _entrada(
        periodo=[maio, maio, date(2026, 4, 1), maio],
        matricula=["1", "2", "1", "1"],
        _arquivo=[
            "Quadro 2026.xlsx",
            "Quadro 2026.xlsx",
            "Quadro 2026.xlsx",
            "Quadro 05-2026.xlsx",
        ],
        _ordem_arquivo=["0001|Quadro 2026.xlsx"] * 3 + ["0002|Quadro 05-2026.xlsx"],
    )
    fluxo = _fluxo(cfg)
    r = fluxo.transformar(entrada).collect()
    # maio veio só do arquivo mensal (mais recente); abril continua do anual
    assert sorted(zip(r["periodo"], r["_arquivo"], strict=True)) == [
        (date(2026, 4, 1), "Quadro 2026.xlsx"),
        (maio, "Quadro 05-2026.xlsx"),
    ]
    (aviso,) = fluxo._validar_sobreposicao()
    assert aviso.severidade == Severidade.AVISO and "05/2026" in aviso.mensagem
    assert "Quadro 05-2026.xlsx" in aviso.mensagem


def test_sobreposicao_erro_nao_descarta_e_bloqueia(cfg):
    maio = date(2026, 5, 1)
    entrada = _entrada(
        periodo=[maio, maio],
        _arquivo=["a.xlsx", "b.xlsx"],
        _ordem_arquivo=["1|a", "2|b"],
    )
    fluxo = _fluxo(cfg, sobreposicao="erro")
    assert fluxo.transformar(entrada).collect().height == 2
    (erro,) = fluxo._validar_sobreposicao()
    assert erro.severidade == Severidade.ERRO
