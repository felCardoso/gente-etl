"""Orquestração: executa os fluxos na ordem das dependências, consolida, valida e grava.

A CLI só apresenta; toda a lógica está aqui para poder ser testada e reutilizada
(ex.: chamar ``executar`` de um notebook).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import polars as pl

from gente_etl.comum import limpeza as lp
from gente_etl.config import Config
from gente_etl.fluxos import REGISTRO, Contexto
from gente_etl.io.excel import ResultadoLeitura
from gente_etl.io.saida import gravar, preparar_para_saida
from gente_etl.validacao import regras
from gente_etl.validacao.resultado import Resultado, Severidade, pior


class ErroPipeline(Exception):
    pass


class Observador(Protocol):
    """Recebe eventos do pipeline (a CLI implementa isto com Rich)."""

    def etapa_iniciada(self, etapa: str, descricao: str) -> None: ...
    def etapa_concluida(self, etapa: str, linhas: int | None, segundos: float) -> None: ...
    def arquivo_lido(self, leitura: ResultadoLeitura) -> None: ...


class _Silencioso:
    def etapa_iniciada(self, etapa: str, descricao: str) -> None:
        pass

    def etapa_concluida(self, etapa: str, linhas: int | None, segundos: float) -> None:
        pass

    def arquivo_lido(self, leitura: ResultadoLeitura) -> None:
        pass


@dataclass
class ResumoFluxo:
    nome: str
    titulo: str
    linhas: int
    colunas: int
    segundos: float


@dataclass
class Execucao:
    referencia: date
    inicio: datetime
    fluxos: list[ResumoFluxo] = field(default_factory=list)
    validacoes: list[Resultado] = field(default_factory=list)
    consolidado: pl.DataFrame | None = None
    arquivos_gravados: list[Path] = field(default_factory=list)
    relatorio: Path | None = None
    gravado: bool = False
    segundos: float = 0.0

    @property
    def severidade(self) -> Severidade:
        return pior(self.validacoes)


# --------------------------------------------------------------------------------------
def ordenar_fluxos(selecionados: list[str], cfg: Config) -> list[str]:
    """Ordem topológica, incluindo dependências ativas que não foram pedidas explicitamente."""
    desconhecidos = [s for s in selecionados if s not in REGISTRO]
    if desconhecidos:
        raise ErroPipeline(
            f"Fluxo(s) desconhecido(s): {', '.join(desconhecidos)}. "
            f"Disponíveis: {', '.join(REGISTRO)}"
        )
    ordem: list[str] = []
    visitando: set[str] = set()

    def visitar(nome: str) -> None:
        if nome in ordem:
            return
        if nome in visitando:
            raise ErroPipeline(f"Dependência circular envolvendo {nome}")
        visitando.add(nome)
        for dep in REGISTRO[nome].depende_de:
            if cfg.fluxo(dep).ativo:
                visitar(dep)
        visitando.discard(nome)
        ordem.append(nome)

    for s in selecionados:
        visitar(s)
    return ordem


def fluxos_ativos(cfg: Config) -> list[str]:
    return [n for n in REGISTRO if cfg.fluxo(n).ativo]


def consolidar(ctx: Contexto, nomes: list[str]) -> pl.DataFrame:
    """Empilha as bases no layout do esquema + coluna de origem (``base``)."""
    cfg = ctx.config
    col_base = cfg.geral.coluna_base
    esperado = {c.nome: lp.DTYPES[c.tipo] for c in cfg.esq.coluna}
    partes = []
    for nome in nomes:
        df = ctx.resultados[nome]
        divergentes = [
            f"{c} ({df.schema[c]}, esperado {esperado[c]})"
            for c in df.columns
            if c in esperado and df.schema[c] != esperado[c]
        ]
        if divergentes:
            raise ErroPipeline(
                f"Fluxo '{nome}' gerou coluna(s) com tipo diferente do esquema: "
                + ", ".join(divergentes)
                + ". Ajuste o cálculo no fluxo ou o tipo em esquema.toml."
            )
        partes.append(
            df.lazy()
            .pipe(lp.garantir_colunas, cfg.esq, manter_internas=False)
            .with_columns(pl.lit(cfg.rotulo(nome)).alias(col_base))
            .select([col_base, *cfg.esq.nomes])
        )
    return pl.concat(partes, how="vertical").collect()


def _historico(cfg: Config) -> Path:
    return cfg.pasta_relatorios / "historico.json"


def ler_historico(cfg: Config) -> list[dict]:
    arq = _historico(cfg)
    if not arq.exists():
        return []
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _salvar_historico(cfg: Config, execucao: Execucao, contagens: dict[str, int]) -> None:
    hist = ler_historico(cfg)
    hist.append(
        {
            "executado_em": execucao.inicio.isoformat(timespec="seconds"),
            "referencia": execucao.referencia.isoformat(),
            "linhas": contagens,
        }
    )
    arq = _historico(cfg)
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(json.dumps(hist[-100:], ensure_ascii=False, indent=2), encoding="utf-8")


def _contagens(consolidado: pl.DataFrame, col_base: str) -> dict[str, int]:
    return dict(consolidado.group_by(col_base).len().sort(col_base).iter_rows())


def gravar_relatorio(cfg: Config, execucao: Execucao) -> Path:
    """Grava JSON com todas as validações + CSV de amostra de cada problema."""
    carimbo = execucao.inicio.strftime("%Y%m%d-%H%M%S")
    pasta = cfg.pasta_relatorios / carimbo
    pasta.mkdir(parents=True, exist_ok=True)
    itens = []
    for i, r in enumerate(execucao.validacoes, 1):
        item = r.como_dict()
        if r.amostra is not None and r.amostra.height:
            arq = pasta / f"{i:02d}-{r.escopo}-{r.regra}.csv"
            r.amostra.select(
                [c for c in r.amostra.columns if c != "_encontrado_referencia"]
            ).write_csv(arq, separator=cfg.csv.separador, include_bom=True)
            item["amostra"] = arq.name
        itens.append(item)
    resumo = {
        "executado_em": execucao.inicio.isoformat(timespec="seconds"),
        "referencia": execucao.referencia.isoformat(),
        "segundos": round(execucao.segundos, 2),
        "gravado": execucao.gravado,
        "arquivos": [str(p) for p in execucao.arquivos_gravados],
        "fluxos": [f.__dict__ for f in execucao.fluxos],
        "validacoes": itens,
    }
    relatorio = pasta / "relatorio.json"
    relatorio.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    return relatorio


# --------------------------------------------------------------------------------------
def executar(
    cfg: Config,
    fluxos: list[str] | None = None,
    referencia: date | None = None,
    usar_cache: bool = True,
    simular: bool = False,
    forcar: bool = False,
    observador: Observador | None = None,
) -> Execucao:
    obs = observador or _Silencioso()
    t0 = time.perf_counter()
    execucao = Execucao(referencia=referencia or date.today(), inicio=datetime.now())
    ctx = Contexto(
        config=cfg, referencia=execucao.referencia, usar_cache=usar_cache, ao_ler=obs.arquivo_lido
    )

    pedidos = fluxos or fluxos_ativos(cfg)
    inativos = [f for f in pedidos if f in REGISTRO and not cfg.fluxo(f).ativo]
    if inativos:
        raise ErroPipeline(f"Fluxo(s) inativo(s) na configuração: {', '.join(inativos)}")
    ordem = ordenar_fluxos(pedidos, cfg)

    # 1) Fluxos -------------------------------------------------------------------------
    for nome in ordem:
        classe = REGISTRO[nome]
        obs.etapa_iniciada(nome, classe.titulo)
        t = time.perf_counter()
        fluxo = classe(ctx)
        df = fluxo.executar()
        ctx.resultados[nome] = df
        execucao.validacoes += fluxo.validar(df)
        seg = time.perf_counter() - t
        execucao.fluxos.append(ResumoFluxo(nome, classe.titulo, df.height, df.width, seg))
        obs.etapa_concluida(nome, df.height, seg)

    # 2) Consolidação ------------------------------------------------------------------
    obs.etapa_iniciada("consolidar", "Consolidando e validando")
    t = time.perf_counter()
    col_base = cfg.geral.coluna_base
    for nome, df in ctx.resultados.items():
        publicas = {c for c in df.columns if not c.startswith("_")}
        fora = sorted(publicas - set(cfg.esq.nomes))
        if fora:
            execucao.validacoes.append(
                Resultado(
                    "coluna_fora_esquema",
                    nome,
                    Severidade.AVISO,
                    f"Coluna(s) {fora} criada(s) no fluxo mas ausente(s) do esquema: descartada(s).",
                )
            )
    consolidado = consolidar(ctx, ordem)
    execucao.consolidado = consolidado
    contagens = _contagens(consolidado, col_base)

    v = cfg.validacao
    execucao.validacoes += regras.contrato_saida(consolidado, cfg.esq, col_base)
    if "quadro" in ctx.resultados:
        execucao.validacoes += regras.reconciliacao_quadro(
            ctx.resultados["quadro"],
            ctx.resultados.get("admitidos"),
            ctx.resultados.get("demitidos"),
            v.tolerancia_reconciliacao,
            v.situacoes_fora_hc,
        )
    hist = ler_historico(cfg)
    execucao.validacoes += regras.variacao_volume(
        contagens, hist[-1]["linhas"] if hist else None, v.variacao_maxima_volume
    )
    obs.etapa_concluida("consolidar", consolidado.height, time.perf_counter() - t)

    # 3) Gravação ----------------------------------------------------------------------
    bloqueado = v.bloquear_em_erro and execucao.severidade >= Severidade.ERRO and not forcar
    if not simular and not bloqueado:
        obs.etapa_iniciada("gravar", "Gravando arquivos")
        t = time.perf_counter()
        execucao.arquivos_gravados += gravar(
            preparar_para_saida(consolidado, cfg), cfg.geral.nome_base, cfg
        )
        if cfg.geral.gravar_por_fluxo:
            for nome in ordem:
                parte = consolidado.filter(pl.col(col_base) == cfg.rotulo(nome))
                execucao.arquivos_gravados += gravar(
                    preparar_para_saida(parte, cfg), f"{cfg.geral.nome_base}_{nome}", cfg
                )
        execucao.gravado = True
        # Histórico só guarda execuções completas (todos os fluxos ativos),
        # para a comparação de volume ser justa.
        if set(ordem) == set(fluxos_ativos(cfg)):
            _salvar_historico(cfg, execucao, contagens)
        obs.etapa_concluida("gravar", None, time.perf_counter() - t)

    execucao.segundos = time.perf_counter() - t0
    execucao.relatorio = gravar_relatorio(cfg, execucao)
    return execucao
