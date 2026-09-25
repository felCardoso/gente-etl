from datetime import date

import fastexcel
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
    assert parquet.columns == ["BASE", *[c.nome_saida for c in cfg.esq.coluna]]
    assert csv.columns == parquet.columns and csv.height == parquet.height
    assert set(parquet["BASE"]) == {
        "QUADRO",
        "ADMITIDOS",
        "DEMITIDOS",
        "MOVIMENTACOES",
        "ORCADO",
        "QUADRO-TERCEIROS",
        "ADMITIDOS-TERCEIROS",
        "DEMITIDOS-TERCEIROS",
    }

    # admitido e demitido no mesmo mês: não está em foto nenhuma, fica só com os
    # dados da própria base (demitidos: chapa, data e motivo)
    adm = parquet.filter(pl.col("BASE") == "ADMITIDOS")
    todos_dem = parquet.filter(pl.col("BASE") == "DEMITIDOS")
    mesmo_mes = todos_dem.join(adm.select("matricula", "periodo"), on=["matricula", "periodo"])
    assert mesmo_mes.height == 2  # um por mês com movimento no demo
    assert mesmo_mes["nome"].null_count() == mesmo_mes.height
    assert mesmo_mes["centro_custo"].null_count() == mesmo_mes.height

    # enriquecimento: demitidos só têm chapa/data/motivo na origem; o resto vem do quadro
    dem = todos_dem.join(mesmo_mes.select("matricula"), on="matricula", how="anti")
    assert dem["nome"].null_count() == 0 and dem["centro_custo"].null_count() == 0
    assert dem["tempo_empresa_meses"].null_count() == 0

    # CHAVE M: dd/MM/yyyy do período + "_" + matrícula; CHAVE M-1: mês anterior
    assert dem["CHAVE M"].to_list() == [
        f"{p:%d/%m/%Y}_{m}" for p, m in zip(dem["periodo"], dem["matricula"], strict=True)
    ]
    assert dem["CHAVE M-1"].to_list() == [
        f"{p.replace(month=p.month - 1) if p.month > 1 else p.replace(year=p.year - 1, month=12):%d/%m/%Y}_{m}"
        for p, m in zip(dem["periodo"], dem["matricula"], strict=True)
    ]
    # demitido não está na foto do mês da saída: veio da foto do mês anterior
    q_chaves = set(parquet.filter(pl.col("BASE") == "QUADRO")["CHAVE M"])
    assert not set(todos_dem["CHAVE M"]) & q_chaves
    assert set(dem["CHAVE M-1"]) <= q_chaves
    assert not set(mesmo_mes["CHAVE M-1"]) & q_chaves
    # os casos do mesmo mês não contam como falha de merge
    cobertura = {r.escopo: r for r in ex.validacoes if r.regra == "cobertura_quadro"}
    assert set(cobertura) == {"admitidos", "demitidos", "movimentacoes"}
    assert all(r.ocorrencias == 0 and r.severidade == Severidade.OK for r in cobertura.values())
    assert "2 linha(s) sem foto" in cobertura["demitidos"].mensagem
    assert "2 linha(s) sem foto" in cobertura["admitidos"].mensagem

    # terceiros: uma base só, separada pela coluna TIPO; TIPO não vai para a saída
    terc = parquet.filter(pl.col("BASE").str.ends_with("-TERCEIROS"))
    tipos = _tipos_terceiros(cfg)
    assert dict(terc.group_by("BASE").len().iter_rows()) == {
        "QUADRO-TERCEIROS": tipos["Ativo"],
        "ADMITIDOS-TERCEIROS": tipos["Admitido"],
        "DEMITIDOS-TERCEIROS": tipos["Demitido"],
    }
    assert "TIPO" not in parquet.columns and "_classificacao" not in parquet.columns

    # quadro: "Quadro 2026.xlsx" (jun+jul) + "Quadro 08-2026.xlsx" (período pelo nome)
    q = parquet.filter(pl.col("BASE") == "QUADRO")
    assert sorted(q["periodo"].unique().to_list()) == [date(2026, 6, 1), date(2026, 7, 1), REF]

    # reconciliação fecha com os dados simulados
    rec = [r for r in ex.validacoes if r.regra == "reconciliacao"]
    assert rec and rec[0].severidade == Severidade.OK

    # relatório e histórico
    assert ex.relatorio.exists()
    assert (
        ler_historico(cfg)[-1]["linhas"]["QUADRO"]
        == parquet.filter(pl.col("BASE") == "QUADRO").height
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
    # terceiros gera três rótulos: o arquivo do fluxo traz os três
    terc = pl.read_parquet(cfg.geral.pasta_saida / "base_gente_terceiros.parquet")
    assert set(terc["BASE"]) == {"QUADRO-TERCEIROS", "ADMITIDOS-TERCEIROS", "DEMITIDOS-TERCEIROS"}


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


def _terceiros(ex):
    return [r for r in ex.validacoes if r.escopo.startswith("terceiros")]


def _tipos_terceiros(cfg) -> dict[str, int]:
    """Quantas linhas de cada TIPO a planilha fictícia de terceiros tem."""
    origem = (
        fastexcel.read_excel(cfg.raiz / "dados/terceiros/terceiros.xlsx").load_sheet(0).to_polars()
    )
    return dict(origem.group_by("TIPO").len().iter_rows())


def test_terceiros_sem_classificacao_da_erro(cfg):
    cfg.fluxos["terceiros"].parametros.pop("coluna_classificacao")
    ex = executar(cfg, fluxos=["terceiros"], referencia=REF, simular=True)
    (r,) = [r for r in _terceiros(ex) if r.regra == "terceiros_sem_classificacao"]
    assert r.severidade == Severidade.ERRO and r.ocorrencias == sum(_tipos_terceiros(cfg).values())
    assert "coluna_classificacao" in r.mensagem


def test_terceiros_valor_sem_rotulo_da_erro(cfg):
    del cfg.fluxos["terceiros"].parametros["classificacao"]["DEMITIDO"]
    ex = executar(cfg, fluxos=["terceiros"], referencia=REF, simular=True)
    (r,) = [r for r in _terceiros(ex) if r.regra == "terceiros_sem_classificacao"]
    assert r.ocorrencias == _tipos_terceiros(cfg)["Demitido"]
    assert set(r.amostra["_classificacao"]) == {"Demitido"}


def test_terceiros_demitidos_chave_periodo_e_nome(cfg, monkeypatch):
    from gente_etl.fluxos.terceiros import Terceiros

    original = Terceiros.transformar

    def mesmo_nome(self, lf):
        # todos com o mesmo nome: só DEMITIDOS-TERCEIROS (período + nome) acusa
        return original(self, lf).with_columns(pl.lit("Fulano de Tal").alias("nome"))

    monkeypatch.setattr(Terceiros, "transformar", mesmo_nome)
    ex = executar(cfg, fluxos=["terceiros"], referencia=REF, simular=True)
    dup = [r for r in _terceiros(ex) if r.regra == "chave_duplicada"]
    assert [r.escopo for r in dup] == ["terceiros/DEMITIDOS-TERCEIROS"]
    assert dup[0].ocorrencias == _tipos_terceiros(cfg)["Demitido"]
