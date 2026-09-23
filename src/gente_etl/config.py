"""Leitura e validação da configuração (TOML).

Dois arquivos:

* ``config.toml``  - caminhos locais (OneDrive/SharePoint sincronizado), opções de saída
  e parâmetros de cada fluxo. **Cada pessoa tem o seu** e ele não vai para o git.
* ``esquema.toml`` - contrato de colunas da base consolidada (nome interno, nome de saída,
  tipo e aliases de cabeçalho). É versionado, pois define o que os modelos semânticos
  recebem.

Caminhos relativos são resolvidos a partir da pasta do próprio ``config.toml``.
Variáveis de ambiente (``%OneDriveCommercial%``, ``${USERPROFILE}``) e ``~`` são expandidas.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

TipoColuna = Literal["texto", "inteiro", "decimal", "data", "booleano"]
FormatoSaida = Literal["parquet", "csv"]

CAMINHO_PADRAO = Path("config/config.toml")
VARIAVEL_AMBIENTE = "GENTE_ETL_CONFIG"


class ErroConfiguracao(Exception):
    """Erro amigável de configuração (mostrado sem traceback na CLI)."""


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


def expandir_caminho(valor: str | Path, base: Path) -> Path:
    """Expande ``~``, variáveis de ambiente (``%VAR%`` e ``${VAR}``) e resolve relativo a ``base``."""
    # %VAR% tratado aqui (sem diferenciar maiúsculas) para funcionar igual em qualquer SO.
    ambiente = {k.upper(): v for k, v in os.environ.items()}
    texto = re.sub(
        r"%([^%/\\]+)%", lambda m: ambiente.get(m.group(1).upper(), m.group(0)), str(valor)
    )
    texto = os.path.expanduser(os.path.expandvars(texto))
    caminho = Path(texto)
    return caminho if caminho.is_absolute() else (base / caminho)


# --------------------------------------------------------------------------------------
# Esquema (contrato de colunas)
# --------------------------------------------------------------------------------------
class Coluna(_Modelo):
    nome: str = Field(description="Nome interno (snake_case) usado no código dos fluxos.")
    tipo: TipoColuna
    saida: str | None = Field(default=None, description="Nome na base final (modelo semântico).")
    aliases: list[str] = Field(default_factory=list, description="Cabeçalhos aceitos na origem.")
    zeros_esquerda: int | None = Field(default=None, ge=1, description="Completa texto com zeros.")
    maiusculas: bool = False
    descricao: str = ""

    @property
    def nome_saida(self) -> str:
        return self.saida or self.nome


class Esquema(_Modelo):
    coluna: list[Coluna]

    @model_validator(mode="after")
    def _nomes_unicos(self) -> Esquema:
        for atributo in ("nome", "nome_saida"):
            vistos: set[str] = set()
            for c in self.coluna:
                valor = getattr(c, atributo)
                if valor in vistos:
                    raise ValueError(f"Coluna duplicada no esquema ({atributo}): {valor!r}")
                vistos.add(valor)
        return self

    @property
    def nomes(self) -> list[str]:
        return [c.nome for c in self.coluna]

    @property
    def por_nome(self) -> dict[str, Coluna]:
        return {c.nome: c for c in self.coluna}

    def mapa_saida(self) -> dict[str, str]:
        return {c.nome: c.nome_saida for c in self.coluna}


# --------------------------------------------------------------------------------------
# Configuração geral
# --------------------------------------------------------------------------------------
class ConfigCsv(_Modelo):
    separador: str = ";"
    decimal_virgula: bool = True
    bom: bool = Field(default=True, description="BOM UTF-8 ajuda Excel/Power Query com acentos.")
    formato_data: str = "%Y-%m-%d"

    @model_validator(mode="after")
    def _separador_compativel(self) -> ConfigCsv:
        if self.decimal_virgula and self.separador == ",":
            raise ValueError("Com decimal_virgula = true o separador não pode ser ','. Use ';'.")
        return self


class ConfigGeral(_Modelo):
    pasta_saida: Path
    pasta_cache: Path = Path(".cache")
    pasta_relatorios: Path | None = None
    nome_base: str = "base_gente"
    formatos: list[FormatoSaida] = Field(default_factory=lambda: ["parquet", "csv"])
    gravar_por_fluxo: bool = Field(default=False, description="Grava também um arquivo por fluxo.")
    coluna_base: str = Field(default="base", description="Coluna que identifica a origem da linha.")
    leitura_paralela: int = Field(default=4, ge=1, le=32)


class ConfigValidacao(_Modelo):
    bloquear_em_erro: bool = True
    tolerancia_reconciliacao: float = Field(default=0.0, ge=0, description="Proporção. 0.01 = 1%.")
    variacao_maxima_volume: float = Field(default=0.2, ge=0, description="Vs. última execução.")
    cobertura_minima_enriquecimento: float = Field(default=0.95, ge=0, le=1)
    linhas_amostra: int = Field(default=50, ge=1)
    situacoes_fora_hc: list[str] = Field(
        default_factory=list,
        description="Situações do quadro que não contam como HC na reconciliação (ex.: 'D').",
    )


class ConfigEnriquecimento(_Modelo):
    ativo: bool = False
    chave: list[str] = Field(default_factory=lambda: ["chave_m"])
    colunas: list[str] = Field(
        default_factory=list, description="Colunas a trazer do quadro. Vazio = todas em comum."
    )
    ignorar: list[str] = Field(default_factory=lambda: ["periodo"])


class ConfigFluxo(_Modelo):
    ativo: bool = True
    rotulo: str | None = Field(default=None, description="Valor da coluna 'base'. Padrão: NOME.")
    arquivos: list[str] = Field(default_factory=list, description="Caminhos ou padrões glob.")
    aba: str | int = Field(default=0, description="Nome, índice (0 = primeira) ou '*' (todas).")
    linha_cabecalho: int = Field(default=0, ge=0)
    obrigatorias: list[str] = Field(default_factory=list)
    chave: list[str] = Field(default_factory=list)
    enriquecer: ConfigEnriquecimento = Field(default_factory=ConfigEnriquecimento)
    parametros: dict[str, Any] = Field(
        default_factory=dict, description="Parâmetros livres usados pela lógica do fluxo."
    )

    @field_validator("arquivos", mode="before")
    @classmethod
    def _aceita_texto(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class Config(_Modelo):
    geral: ConfigGeral
    csv: ConfigCsv = Field(default_factory=ConfigCsv)
    validacao: ConfigValidacao = Field(default_factory=ConfigValidacao)
    esquema_arquivo: Path = Field(default=Path("esquema.toml"), alias="esquema")
    fluxos: dict[str, ConfigFluxo] = Field(default_factory=dict)

    # Preenchidos por ``carregar_config``
    raiz: Path = Field(default=Path("."), exclude=True)
    esquema_carregado: Esquema | None = Field(default=None, exclude=True)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @property
    def esq(self) -> Esquema:
        if self.esquema_carregado is None:  # pragma: no cover - proteção
            raise ErroConfiguracao("Esquema não carregado.")
        return self.esquema_carregado

    @property
    def pasta_relatorios(self) -> Path:
        return self.geral.pasta_relatorios or (self.geral.pasta_saida / "relatorios")

    def fluxo(self, nome: str) -> ConfigFluxo:
        return self.fluxos.get(nome) or ConfigFluxo(ativo=False)

    def rotulo(self, nome: str) -> str:
        return self.fluxo(nome).rotulo or nome.upper()


def _ler_toml(caminho: Path) -> dict[str, Any]:
    try:
        with caminho.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as e:
        raise ErroConfiguracao(f"Arquivo não encontrado: {caminho}") from e
    except tomllib.TOMLDecodeError as e:
        raise ErroConfiguracao(
            f"TOML inválido em {caminho}: {e}\n"
            "Dica: em caminhos Windows use aspas simples ('C:\\pasta') ou barras normais (C:/pasta)."
        ) from e


def _formatar_erro(e: ValidationError, caminho: Path) -> str:
    linhas = [f"Configuração inválida em {caminho}:"]
    for erro in e.errors():
        local = ".".join(str(p) for p in erro["loc"])
        linhas.append(f"  • {local}: {erro['msg']}")
    return "\n".join(linhas)


def carregar_esquema(caminho: Path) -> Esquema:
    try:
        return Esquema.model_validate(_ler_toml(caminho))
    except ValidationError as e:
        raise ErroConfiguracao(_formatar_erro(e, caminho)) from e


def localizar_config(caminho: Path | None = None) -> Path:
    if caminho is not None:
        return caminho
    if env := os.environ.get(VARIAVEL_AMBIENTE):
        return Path(env)
    return CAMINHO_PADRAO


def carregar_config(caminho: Path | None = None) -> Config:
    caminho = localizar_config(caminho).resolve()
    dados = _ler_toml(caminho)
    try:
        cfg = Config.model_validate(dados)
    except ValidationError as e:
        raise ErroConfiguracao(_formatar_erro(e, caminho)) from e

    raiz = caminho.parent
    cfg.raiz = raiz
    g = cfg.geral
    g.pasta_saida = expandir_caminho(g.pasta_saida, raiz)
    g.pasta_cache = expandir_caminho(g.pasta_cache, raiz)
    if g.pasta_relatorios is not None:
        g.pasta_relatorios = expandir_caminho(g.pasta_relatorios, raiz)
    cfg.esquema_arquivo = expandir_caminho(cfg.esquema_arquivo, raiz)
    cfg.esquema_carregado = carregar_esquema(cfg.esquema_arquivo)

    _checar_colunas_referenciadas(cfg, caminho)
    return cfg


def _checar_colunas_referenciadas(cfg: Config, caminho: Path) -> None:
    """Garante que obrigatórias/chaves/enriquecimento citam colunas que existem no esquema."""
    conhecidas = set(cfg.esq.nomes)
    problemas: list[str] = []
    for nome, f in cfg.fluxos.items():
        campos = {
            "obrigatorias": f.obrigatorias,
            "chave": f.chave,
            "enriquecer.chave": f.enriquecer.chave,
            "enriquecer.colunas": f.enriquecer.colunas,
        }
        for campo, cols in campos.items():
            for c in cols:
                if c not in conhecidas:
                    problemas.append(
                        f"  • fluxos.{nome}.{campo}: coluna {c!r} não existe no esquema"
                    )
    if problemas:
        raise ErroConfiguracao(f"Configuração inválida em {caminho}:\n" + "\n".join(problemas))
