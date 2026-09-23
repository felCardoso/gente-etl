from pathlib import Path

import pytest

from gente_etl.config import ErroConfiguracao, carregar_config, expandir_caminho
from gente_etl.demo import modelo

RAIZ = Path(__file__).resolve().parents[1]


def test_modelos_do_pacote_iguais_aos_da_pasta_config():
    """Evita que config/ e src/gente_etl/modelos/ fiquem diferentes."""
    assert (RAIZ / "config/esquema.toml").read_text(encoding="utf-8") == modelo("esquema.toml")
    assert (RAIZ / "config/config.exemplo.toml").read_text(encoding="utf-8") == modelo(
        "config.toml"
    )


def test_carrega_config_demo(cfg, demo):
    assert cfg.geral.pasta_saida == demo / "saida"
    assert cfg.fluxo("admitidos").enriquecer.ativo
    assert cfg.rotulo("orcado") == "ORCADO"
    assert cfg.rotulo("quadro") == "QUADRO"
    assert not cfg.fluxo("inexistente").ativo


def test_expande_variaveis_de_ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("ONEDRIVE_TESTE", "/onedrive")
    assert expandir_caminho("%ONEDRIVE_TESTE%/x", tmp_path) == Path("/onedrive/x")
    assert expandir_caminho("${ONEDRIVE_TESTE}/y", tmp_path) == Path("/onedrive/y")
    assert expandir_caminho("rel/z", tmp_path) == tmp_path / "rel/z"


def _editar(demo: Path, antes: str, depois: str) -> Path:
    arq = demo / "config.toml"
    texto = arq.read_text(encoding="utf-8")
    assert antes in texto
    arq.write_text(texto.replace(antes, depois, 1), encoding="utf-8")
    return arq


def test_coluna_desconhecida_em_chave_gera_erro_claro(demo):
    arq = _editar(demo, 'chave = ["periodo", "matricula"]', 'chave = ["periodo", "chapa"]')
    with pytest.raises(ErroConfiguracao, match=r"fluxos.quadro.chave: coluna 'chapa'"):
        carregar_config(arq)


def test_campo_desconhecido_e_rejeitado(demo):
    arq = _editar(demo, "[geral]", "[geral]\nsaida_errada = 1")
    with pytest.raises(ErroConfiguracao, match="saida_errada"):
        carregar_config(arq)


def test_toml_invalido_da_dica_de_caminho_windows(tmp_path):
    arq = tmp_path / "config.toml"
    arq.write_text('[geral]\npasta_saida = "C:\\Users\\x"\n', encoding="utf-8")
    with pytest.raises(ErroConfiguracao, match="aspas simples"):
        carregar_config(arq)


def test_csv_virgula_com_decimal_virgula_e_invalido(demo):
    arq = _editar(demo, 'separador = ";"', 'separador = ","')
    with pytest.raises(ErroConfiguracao, match="separador"):
        carregar_config(arq)


def test_arquivo_inexistente(tmp_path):
    with pytest.raises(ErroConfiguracao, match="não encontrado"):
        carregar_config(tmp_path / "nao_existe.toml")


def test_variavel_percentual_ignora_maiusculas(monkeypatch, tmp_path):
    monkeypatch.setenv("ONEDRIVECOMMERCIAL", "/od")
    assert expandir_caminho("%OneDriveCommercial%/b", tmp_path) == Path("/od/b")
    assert expandir_caminho("%NAO_EXISTE_XYZ%/b", tmp_path) == tmp_path / "%NAO_EXISTE_XYZ%/b"
