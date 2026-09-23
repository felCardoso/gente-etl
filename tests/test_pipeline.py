from datetime import date

import polars as pl
import pytest

from gente_etl.config import carregar_config
from gente_etl.pipeline import ErroPipeline, executar, ler_historico, ordenar_fluxos
from gente_etl.validacao.resultado import Severidade

REF = date(2026, 8, 1)


def test_execucao_completa_demo(cfg):
    ex = executar(cfg, referencia=REF)
    assert ex.gravado
    assert ex.severidade < Severidade.ERRO
    saida = cfg.geral.pasta_saida
    parquet = pl.read_parquet(saida / "base_gente.parquet")
    csv = pl.read_csv(saida / "base_gente.csv", separator=";", infer_schema=False)

    # contrato: coluna de origem + colunas do esquema, na ordem
    assert parquet.columns == ["base", *[c.nome_saida for c in cfg.esq.coluna]]
    assert csv.columns == parquet.columns and csv.height == parquet.height
    assert set(parquet["base"]) == {
        "QUADRO",
        "ADMITIDOS",
        "DEMITIDOS",
        "MOVIMENTACOES",
        "TERCEIROS",
        "ORCADO",
    }

    # enriquecimento: demitidos só têm chapa/data/motivo na origem; o resto vem do quadro
    dem = parquet.filter(pl.col("base") == "DEMITIDOS")
    assert dem["nome"].null_count() == 0 and dem["centro_custo"].null_count() == 0
    assert dem["tempo_empresa_meses"].null_count() == 0

    # CHAVE M: dd/MM/yyyy do período + "_" + matrícula; eventos acham a foto do mesmo mês
    assert dem["CHAVE M"].to_list() == [
        f"{p:%d/%m/%Y}_{m}" for p, m in zip(dem["periodo"], dem["matricula"], strict=True)
    ]
    cobertura = {r.escopo: r for r in ex.validacoes if r.regra == "cobertura_quadro"}
    assert set(cobertura) == {"admitidos", "demitidos", "movimentacoes"}
    assert all(r.ocorrencias == 0 for r in cobertura.values())

    # quadro: "Quadro 2026.xlsx" (jun+jul) + "Quadro 08-2026.xlsx" (período pelo nome)
    q = parquet.filter(pl.col("base") == "QUADRO")
    assert sorted(q["periodo"].unique().to_list()) == [date(2026, 6, 1), date(2026, 7, 1), REF]

    # reconciliação fecha com os dados simulados
    rec = [r for r in ex.validacoes if r.regra == "reconciliacao"]
    assert rec and rec[0].severidade == Severidade.OK

    # relatório e histórico
    assert ex.relatorio.exists()
    assert (
        ler_historico(cfg)[-1]["linhas"]["QUADRO"]
        == parquet.filter(pl.col("base") == "QUADRO").height
    )


def test_nomes_de_saida_do_esquema_sao_aplicados(demo):
    esq = demo / "esquema.toml"
    esq.write_text(
        esq.read_text(encoding="utf-8").replace(
            'nome = "matricula"\n', 'nome = "matricula"\nsaida = "CHAPA"\n'
        ),
        encoding="utf-8",
    )
    cfg = carregar_config(demo / "config.toml")
    executar(cfg, referencia=REF)
    assert "CHAPA" in pl.read_parquet_schema(cfg.geral.pasta_saida / "base_gente.parquet")


def test_erro_bloqueia_gravacao_e_forcar_grava(demo):
    cfg = carregar_config(demo / "config.toml")
    cfg.fluxos["quadro"].chave = ["matricula"]  # 3 períodos -> matrícula repete -> ERRO
    ex = executar(cfg, referencia=REF)
    assert ex.severidade == Severidade.ERRO and not ex.gravado
    assert not (cfg.geral.pasta_saida / "base_gente.parquet").exists()
    assert any(r.regra == "chave_duplicada" and r.amostra is not None for r in ex.validacoes)

    ex = executar(cfg, referencia=REF, forcar=True)
    assert ex.gravado and (cfg.geral.pasta_saida / "base_gente.parquet").exists()


def test_simular_nao_grava(cfg):
    ex = executar(cfg, referencia=REF, simular=True)
    assert not ex.gravado and ex.consolidado is not None
    assert not (cfg.geral.pasta_saida / "base_gente.parquet").exists()
    assert ex.relatorio.exists()


def test_fluxo_pedido_puxa_dependencias(cfg):
    assert ordenar_fluxos(["demitidos"], cfg) == ["quadro", "demitidos"]
    ex = executar(cfg, fluxos=["demitidos"], referencia=REF, simular=True)
    assert [f.nome for f in ex.fluxos] == ["quadro", "demitidos"]


def test_fluxo_desconhecido(cfg):
    with pytest.raises(ErroPipeline, match="desconhecido"):
        executar(cfg, fluxos=["quadr"])


def test_gravar_por_fluxo(cfg):
    cfg.geral.gravar_por_fluxo = True
    cfg.geral.formatos = ["parquet"]
    executar(cfg, referencia=REF)
    assert (cfg.geral.pasta_saida / "base_gente_orcado.parquet").exists()
    assert not (cfg.geral.pasta_saida / "base_gente.csv").exists()


def test_segunda_execucao_usa_cache(cfg):
    lidos = []

    class Obs:
        def etapa_iniciada(self, *a): ...
        def etapa_concluida(self, *a): ...
        def arquivo_lido(self, leitura):
            lidos.append(leitura.do_cache)

    executar(cfg, referencia=REF, simular=True, observador=Obs())
    assert not any(lidos)
    lidos.clear()
    executar(cfg, referencia=REF, simular=True, observador=Obs())
    assert lidos and all(lidos)


def test_coluna_com_tipo_errado_no_fluxo_da_erro_claro(cfg, monkeypatch):
    from gente_etl.fluxos.terceiros import Terceiros

    original = Terceiros.transformar
    monkeypatch.setattr(
        Terceiros,
        "transformar",
        lambda self, lf: original(self, lf).with_columns(pl.lit("x").alias("tempo_empresa_meses")),
    )
    with pytest.raises(ErroPipeline, match="tempo_empresa_meses"):
        executar(cfg, referencia=REF, simular=True)
