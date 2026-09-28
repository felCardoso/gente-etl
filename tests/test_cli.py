from typer.testing import CliRunner

from gente_etl.cli import app

runner = CliRunner()


def test_versao():
    r = runner.invoke(app, ["--versao"])
    assert r.exit_code == 0 and "gente-etl" in r.output


def test_executar_demo(demo):
    r = runner.invoke(app, ["executar", "-c", str(demo / "config.toml"), "-r", "2026-08"])
    assert r.exit_code == 0, r.output
    assert "Base gravada" in r.output


def test_executar_com_erro_retorna_1(demo):
    cfg = demo / "config.toml"
    texto = cfg.read_text(encoding="utf-8").replace(
        'chave = ["periodo", "matricula"]', 'chave = ["matricula"]', 1
    )
    cfg.write_text(texto, encoding="utf-8")
    r = runner.invoke(app, ["executar", "-c", str(cfg), "-r", "08/2026"])
    assert r.exit_code == 1
    assert "Gravação bloqueada" in r.output


def test_config_inexistente_retorna_2(tmp_path):
    r = runner.invoke(app, ["executar", "-c", str(tmp_path / "x.toml")])
    assert r.exit_code == 2
    assert "config iniciar" in r.output


def test_referencia_invalida(demo):
    r = runner.invoke(app, ["executar", "-c", str(demo / "config.toml"), "-r", "agosto"])
    assert r.exit_code != 0


def test_fluxos_e_verificar(demo):
    r = runner.invoke(app, ["fluxos", "-c", str(demo / "config.toml")])
    assert r.exit_code == 0 and "Quadro" in r.output
    r = runner.invoke(app, ["config", "verificar", "-c", str(demo / "config.toml")])
    assert r.exit_code == 0, r.output


def test_inspecionar(demo):
    arq = sorted((demo / "dados/quadro").glob("*.xlsx"))[-1]
    r = runner.invoke(
        app,
        ["inspecionar", str(arq), "--esquema", str(demo / "esquema.toml")],
        env={"COLUMNS": "200"},
    )
    assert r.exit_code == 0, r.output
    assert "data_admissao" in r.output  # "Data de Admissão" casou pelo alias


def test_config_iniciar(tmp_path):
    r = runner.invoke(app, ["config", "iniciar", "--pasta", str(tmp_path / "cfg")])
    assert r.exit_code == 0
    assert (tmp_path / "cfg/config.toml").exists() and (tmp_path / "cfg/esquema.toml").exists()


def test_cache_limpar(demo):
    runner.invoke(app, ["executar", "-c", str(demo / "config.toml"), "-s"])
    r = runner.invoke(app, ["cache", "limpar", "-c", str(demo / "config.toml")])
    assert r.exit_code == 0 and "removido" in r.output
