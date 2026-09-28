"""Interface de linha de comando.

gente-etl executar                 # roda todos os fluxos ativos
gente-etl executar quadro admitidos
gente-etl fluxos                   # status dos fluxos e arquivos encontrados
gente-etl inspecionar arquivo.xlsx # mostra cabeçalhos e como casam com o esquema
gente-etl config iniciar | verificar
gente-etl cache limpar
gente-etl demo                     # ambiente de teste com dados fictícios
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from gente_etl import __version__
from gente_etl.comum.limpeza import mapa_aliases, normalizar_nome
from gente_etl.config import (
    Config,
    ErroConfiguracao,
    carregar_config,
    carregar_esquema,
    localizar_config,
)
from gente_etl.fluxos import REGISTRO
from gente_etl.io.excel import (
    ErroLeitura,
    Planilha,
    ResultadoLeitura,
    chave_cache,
    ler_planilha_bruta,
    listar_abas,
    planilhas_da_fonte,
)
from gente_etl.pipeline import ErroPipeline, Execucao, executar
from gente_etl.validacao.resultado import Severidade

console = Console(highlight=False)
app = typer.Typer(
    name="gente-etl",
    help="ETL local das bases de Gente: quadro, admitidos, demitidos, movimentações, "
    "terceiros e orçado.",
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
app_config = typer.Typer(help="Criar e conferir a configuração.", no_args_is_help=True)
app_cache = typer.Typer(help="Gerenciar o cache de leitura dos XLSX.", no_args_is_help=True)
app.add_typer(app_config, name="config")
app.add_typer(app_cache, name="cache")

CORES = {
    Severidade.OK: "green",
    Severidade.INFO: "cyan",
    Severidade.AVISO: "yellow",
    Severidade.ERRO: "bold red",
}
ICONES = {Severidade.OK: "✔", Severidade.INFO: "ℹ", Severidade.AVISO: "▲", Severidade.ERRO: "✖"}

OpcaoConfig = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Arquivo de configuração. Padrão: $GENTE_ETL_CONFIG ou config/config.toml.",
        show_default=False,
    ),
]


# --------------------------------------------------------------------------------------
# Utilidades de apresentação
# --------------------------------------------------------------------------------------
def fmt_int(n: int | None) -> str:
    return "—" if n is None else f"{n:,}".replace(",", ".")


def fmt_seg(s: float) -> str:
    return f"{s:.1f} s".replace(".", ",") if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"


def fmt_bytes(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return (
                f"{n:.0f} {unidade}" if unidade == "B" else f"{n:.1f} {unidade}".replace(".", ",")
            )
        n /= 1024
    return f"{n:.1f} TB"


def erro_fatal(mensagem: str, codigo: int = 2) -> typer.Exit:
    console.print(Panel(mensagem, title="[bold red]Erro", border_style="red", expand=False))
    return typer.Exit(codigo)


def _carregar(config: Path | None) -> Config:
    try:
        return carregar_config(config)
    except ErroConfiguracao as e:
        dica = ""
        if not localizar_config(config).exists():
            dica = "\n\n[dim]Crie uma com[/] [bold]gente-etl config iniciar[/] [dim]ou teste com[/] [bold]gente-etl demo[/]"
        raise erro_fatal(f"{e}{dica}") from None


def _parse_referencia(valor: str | None) -> date | None:
    if not valor:
        return None
    valor = valor.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m", "%m/%Y", "%Y%m"):
        try:
            return datetime.strptime(valor, fmt).date()
        except ValueError:
            continue
    raise typer.BadParameter("Use AAAA-MM, MM/AAAA ou uma data (AAAA-MM-DD / DD/MM/AAAA).")


def _cabecalho(cfg: Config, referencia: date | None = None) -> None:
    ref = referencia or date.today()
    console.print(
        Text.assemble(
            ("gente-etl ", "bold magenta"),
            (f"v{__version__}", "dim"),
            ("  ·  referência ", "dim"),
            (f"{ref:%m/%Y}", "bold"),
            ("  ·  ", "dim"),
            (str(cfg.raiz / "config.toml") if cfg.raiz else "", "dim"),
        )
    )


class ObservadorRich:
    """Mostra o progresso do pipeline com spinners e tempos."""

    NOMES = {"consolidar": "Consolidação", "gravar": "Gravação"}

    def __init__(self, progresso: Progress, detalhes: bool) -> None:
        self.p = progresso
        self.detalhes = detalhes
        self.tarefas: dict[str, TaskID] = {}
        self.titulos: dict[str, str] = {}

    def etapa_iniciada(self, etapa: str, descricao: str) -> None:
        self.titulos[etapa] = descricao
        self.tarefas[etapa] = self.p.add_task(f"[bold]{descricao}[/]", total=1)

    def etapa_concluida(self, etapa: str, linhas: int | None, segundos: float) -> None:
        extra = f" [dim]·[/] {fmt_int(linhas)} linhas" if linhas is not None else ""
        titulo = self.NOMES.get(etapa, self.titulos.get(etapa, etapa))
        self.p.update(
            self.tarefas[etapa],
            completed=1,
            description=f"[green]✔[/] [bold]{titulo}[/]{extra} [dim]({fmt_seg(segundos)})[/]",
        )

    def arquivo_lido(self, leitura: ResultadoLeitura) -> None:
        if not self.detalhes:
            return
        origem = "[cyan]cache[/]" if leitura.do_cache else "[yellow]xlsx[/]"
        aba = f" [dim]› {leitura.planilha.aba}[/]" if leitura.planilha.aba != 0 else ""
        self.p.console.print(
            f"   [dim]↳[/] {leitura.planilha.arquivo.name}{aba} "
            f"[dim]{fmt_int(leitura.df.height)} linhas ·[/] {origem}"
        )


def _tabela_validacoes(execucao: Execucao, mostrar_ok: bool) -> Table | None:
    itens = [r for r in execucao.validacoes if mostrar_ok or r.severidade > Severidade.OK]
    if not itens:
        return None
    t = Table(title="Validações", title_justify="left", title_style="bold", expand=True)
    t.add_column("", width=2)
    t.add_column("Escopo", style="bold", no_wrap=True)
    t.add_column("Regra", style="dim", no_wrap=True)
    t.add_column("Mensagem", ratio=1)
    for r in sorted(itens, key=lambda r: (-r.severidade, r.escopo)):
        cor = CORES[r.severidade]
        t.add_row(f"[{cor}]{ICONES[r.severidade]}[/]", r.escopo, r.regra, f"[{cor}]{r.mensagem}[/]")
    return t


def _tabela_fluxos(execucao: Execucao) -> Table:
    t = Table(title="Fluxos", title_justify="left", title_style="bold")
    t.add_column("Fluxo")
    t.add_column("Linhas", justify="right")
    t.add_column("Colunas", justify="right")
    t.add_column("Tempo", justify="right", style="dim")
    for f in execucao.fluxos:
        t.add_row(f.titulo, fmt_int(f.linhas), str(f.colunas), fmt_seg(f.segundos))
    if execucao.consolidado is not None:
        t.add_section()
        t.add_row(
            "[bold]Base consolidada[/]",
            f"[bold]{fmt_int(execucao.consolidado.height)}[/]",
            str(execucao.consolidado.width),
            fmt_seg(execucao.segundos),
        )
    return t


# --------------------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------------------
def _versao(valor: bool) -> None:
    if valor:
        console.print(f"gente-etl {__version__}")
        raise typer.Exit()


@app.callback()
def principal(
    versao: Annotated[
        bool,
        typer.Option("--versao", "-V", callback=_versao, is_eager=True, help="Mostra a versão."),
    ] = False,
) -> None:
    """[bold magenta]gente-etl[/] · ETL local das bases de Gente com Polars."""


@app.command("executar")
def cmd_executar(
    fluxos: Annotated[
        list[str] | None,
        typer.Argument(
            help="Fluxos a rodar (padrão: todos os ativos). Dependências entram sozinhas."
        ),
    ] = None,
    config: OpcaoConfig = None,
    referencia: Annotated[
        str | None,
        typer.Option("--referencia", "-r", help="Mês de referência (AAAA-MM). Padrão: hoje."),
    ] = None,
    sem_cache: Annotated[
        bool, typer.Option("--sem-cache", help="Relê todos os XLSX, ignorando o cache.")
    ] = False,
    simular: Annotated[
        bool, typer.Option("--simular", "-s", help="Processa e valida, mas não grava a base.")
    ] = False,
    forcar: Annotated[
        bool, typer.Option("--forcar", help="Grava mesmo com validações de ERRO.")
    ] = False,
    detalhes: Annotated[
        bool, typer.Option("--detalhes", "-d", help="Mostra arquivos lidos e validações OK.")
    ] = False,
) -> None:
    """Executa o ETL: lê, trata, valida e grava a base consolidada ([bold]CSV + Parquet[/])."""
    cfg = _carregar(config)
    ref = _parse_referencia(referencia)
    _cabecalho(cfg, ref)

    progresso = Progress(
        SpinnerColumn(style="magenta"),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )
    try:
        with progresso:
            execucao = executar(
                cfg,
                fluxos=fluxos or None,
                referencia=ref,
                usar_cache=not sem_cache,
                simular=simular,
                forcar=forcar,
                observador=ObservadorRich(progresso, detalhes),
            )
    except (ErroPipeline, ErroLeitura, ErroConfiguracao) as e:
        raise erro_fatal(str(e)) from None

    console.print()
    console.print(_tabela_fluxos(execucao))
    tabela_v = _tabela_validacoes(execucao, mostrar_ok=detalhes)
    if tabela_v:
        console.print(tabela_v)
    _painel_final(execucao, cfg, simular)

    if execucao.severidade >= Severidade.ERRO and not execucao.gravado:
        raise typer.Exit(1)


def _painel_final(execucao: Execucao, cfg: Config, simular: bool) -> None:
    contagem = {s: 0 for s in Severidade}
    for r in execucao.validacoes:
        contagem[r.severidade] += 1
    resumo = Text.assemble(
        (f"{contagem[Severidade.ERRO]} erro(s)", CORES[Severidade.ERRO]),
        ("  ·  ", "dim"),
        (f"{contagem[Severidade.AVISO]} aviso(s)", CORES[Severidade.AVISO]),
        ("  ·  ", "dim"),
        (f"{contagem[Severidade.OK]} ok", CORES[Severidade.OK]),
    )
    linhas: list = [resumo, Text()]
    if execucao.gravado:
        titulo, cor = "Base gravada", "green"
        for p in execucao.arquivos_gravados:
            linhas.append(
                Text.assemble(
                    ("  ", ""), (str(p), "bold"), (f"  {fmt_bytes(p.stat().st_size)}", "dim")
                )
            )
        if execucao.severidade >= Severidade.ERRO:
            titulo, cor = "Base gravada com --forcar (há erros)", "yellow"
    elif simular:
        titulo, cor = "Simulação concluída (nada foi gravado)", "cyan"
    else:
        titulo, cor = "Gravação bloqueada", "red"
        linhas.append(Text("Corrija os erros acima ou rode com --forcar.", style="red"))
    if execucao.relatorio:
        linhas += [Text(), Text.assemble(("Relatório: ", "dim"), (str(execucao.relatorio), ""))]
    console.print(Panel(Group(*linhas), title=f"[bold {cor}]{titulo}", border_style=cor))


@app.command("fluxos")
def cmd_fluxos(config: OpcaoConfig = None) -> None:
    """Lista os fluxos, os arquivos encontrados e o estado do cache."""
    cfg = _carregar(config)
    _cabecalho(cfg)
    t = Table(expand=True)
    t.add_column("Fluxo", style="bold")
    t.add_column("Ativo", justify="center")
    t.add_column("Frequência", style="dim")
    t.add_column("Depende de", style="dim")
    t.add_column("Arquivos", justify="right")
    t.add_column("Tamanho", justify="right")
    t.add_column("Modificado em", justify="right")
    t.add_column("Cache", justify="center")
    for nome, classe in REGISTRO.items():
        fc = cfg.fluxo(nome)
        ativo = "[green]sim[/]" if fc.ativo else "[dim]não[/]"
        try:
            planilhas = planilhas_da_fonte(fc, cfg.raiz) if fc.arquivos else []
        except Exception as e:  # arquivo corrompido, sem permissão...
            t.add_row(classe.titulo, ativo, classe.frequencia, "", f"[red]{e}[/]", "", "", "")
            continue
        arquivos = {p.arquivo for p in planilhas}
        if not arquivos:
            t.add_row(
                classe.titulo,
                ativo,
                classe.frequencia,
                ", ".join(classe.depende_de),
                "[red]0[/]" if fc.ativo else "0",
                "",
                "",
                "",
            )
            continue
        tamanho = sum(a.stat().st_size for a in arquivos)
        modificado = max(a.stat().st_mtime for a in arquivos)
        em_cache = sum(
            1
            for p in planilhas
            if any((cfg.geral.pasta_cache / "xlsx").glob(f"*-{chave_cache(p)}.parquet"))
        )
        cache = f"{em_cache}/{len(planilhas)}"
        cache = f"[green]{cache}[/]" if em_cache == len(planilhas) else f"[yellow]{cache}[/]"
        t.add_row(
            classe.titulo,
            ativo,
            classe.frequencia,
            ", ".join(classe.depende_de),
            str(len(arquivos)),
            fmt_bytes(tamanho),
            datetime.fromtimestamp(modificado).strftime("%d/%m/%Y %H:%M"),
            cache,
        )
    console.print(t)


@app.command("inspecionar")
def cmd_inspecionar(
    arquivo: Annotated[Path, typer.Argument(help="Planilha XLSX a inspecionar.", exists=True)],
    aba: Annotated[str | None, typer.Option("--aba", "-a", help="Nome ou índice da aba.")] = None,
    cabecalho: Annotated[int, typer.Option("--cabecalho", help="Linha do cabeçalho (0 = 1ª).")] = 0,
    config: OpcaoConfig = None,
    esquema: Annotated[
        Path | None, typer.Option("--esquema", help="esquema.toml (padrão: o da configuração).")
    ] = None,
) -> None:
    """Mostra abas e cabeçalhos de um XLSX e como eles casam com o [bold]esquema[/].

    Use para descobrir quais [italic]aliases[/] faltam no esquema.toml.
    """
    esq = carregar_esquema(esquema) if esquema else _carregar(config).esq
    abas = listar_abas(arquivo)
    console.print(
        f"[bold]{arquivo.name}[/]  [dim]abas:[/] "
        + ", ".join(f"[cyan]{i}[/]:{a}" for i, a in enumerate(abas))
    )
    alvo: str | int = 0 if aba is None else (int(aba) if aba.isdigit() else aba)
    with console.status("Lendo planilha…"):
        df = ler_planilha_bruta(Planilha(arquivo, alvo, cabecalho), n_linhas=200)

    mapa = mapa_aliases(esq)
    tipos = {c.nome: c.tipo for c in esq.coluna}
    t = Table(title=f"Aba {alvo!r} · colunas da origem", title_justify="left", expand=True)
    t.add_column("Cabeçalho na origem")
    t.add_column("→ Coluna do esquema")
    t.add_column("Tipo", style="dim")
    t.add_column("Preenchimento", justify="right")
    t.add_column("Exemplos", style="dim", ratio=1)
    usados: set[str] = set()
    for col in df.columns:
        destino = mapa.get(normalizar_nome(col))
        duplicado = destino in usados
        if destino and not duplicado:
            usados.add(destino)
        serie = df[col]
        preench = 1 - serie.null_count() / max(len(serie), 1)
        exemplos = ", ".join(str(v) for v in serie.drop_nulls().unique(maintain_order=True).head(3))
        if destino and not duplicado:
            alvo_txt = f"[green]{destino}[/]"
        elif duplicado:
            alvo_txt = f"[yellow]{destino} (já usada — descartada)[/]"
        else:
            alvo_txt = "[yellow]— sem correspondência[/]"
        t.add_row(repr(col), alvo_txt, tipos.get(destino or "", ""), f"{preench:.0%}", exemplos)
    console.print(t)
    ausentes = [c for c in esq.nomes if c not in usados]
    console.print(
        f"[dim]Colunas do esquema não encontradas nesta aba ({len(ausentes)}):[/] "
        + ", ".join(ausentes)
    )
    console.print(
        "[dim]Para casar um cabeçalho, adicione-o em[/] aliases [dim]da coluna em esquema.toml.[/]"
    )


@app_config.command("iniciar")
def cmd_config_iniciar(
    pasta: Annotated[Path, typer.Option("--pasta", "-p", help="Onde criar.")] = Path("config"),
    sobrescrever: Annotated[
        bool, typer.Option("--sobrescrever", help="Substitui existentes.")
    ] = False,
) -> None:
    """Cria [bold]config.toml[/] e [bold]esquema.toml[/] a partir dos modelos."""
    from gente_etl.demo import modelo

    pasta.mkdir(parents=True, exist_ok=True)
    for nome in ("config.toml", "esquema.toml"):
        destino = pasta / nome
        if destino.exists() and not sobrescrever:
            console.print(f"[yellow]•[/] {destino} já existe [dim](mantido)[/]")
            continue
        destino.write_text(modelo(nome), encoding="utf-8")
        console.print(f"[green]✔[/] {destino} criado")
    console.print(
        "\nPróximos passos:\n"
        f"  1. Edite [bold]{pasta / 'config.toml'}[/] com os caminhos do SharePoint sincronizado.\n"
        "  2. Rode [bold]gente-etl config verificar[/].\n"
        "  3. Rode [bold]gente-etl executar --simular[/]."
    )


@app_config.command("verificar")
def cmd_config_verificar(config: OpcaoConfig = None) -> None:
    """Valida a configuração e confere se os arquivos de cada fluxo existem."""
    cfg = _carregar(config)
    console.print(f"[green]✔[/] Configuração válida [dim]({cfg.raiz / 'config.toml'})[/]")
    console.print(
        f"[green]✔[/] Esquema com {len(cfg.esq.coluna)} colunas [dim]({cfg.esquema_arquivo})[/]"
    )
    problemas = 0
    for nome in REGISTRO:
        fc = cfg.fluxo(nome)
        if not fc.ativo:
            console.print(f"[dim]•[/] {nome}: inativo")
            continue
        try:
            planilhas = planilhas_da_fonte(fc, cfg.raiz)
        except Exception as e:
            console.print(f"[red]✖[/] {nome}: {e}")
            problemas += 1
            continue
        if planilhas:
            n_arq = len({p.arquivo for p in planilhas})
            console.print(f"[green]✔[/] {nome}: {n_arq} arquivo(s), {len(planilhas)} aba(s)")
        else:
            problemas += 1
            console.print(f"[red]✖[/] {nome}: nenhum arquivo em {fc.arquivos}")
    saida = cfg.geral.pasta_saida
    if saida.exists():
        console.print(f"[green]✔[/] Pasta de saída: {saida}")
    else:
        console.print(f"[yellow]▲[/] Pasta de saída ainda não existe (será criada): {saida}")
    if problemas:
        raise typer.Exit(1)


@app_cache.command("limpar")
def cmd_cache_limpar(config: OpcaoConfig = None) -> None:
    """Apaga o cache de leitura (os XLSX serão relidos na próxima execução)."""
    cfg = _carregar(config)
    pasta = cfg.geral.pasta_cache
    if not pasta.exists():
        console.print("[dim]Cache já está vazio.[/]")
        return
    tamanho = sum(p.stat().st_size for p in pasta.rglob("*") if p.is_file())
    shutil.rmtree(pasta)
    console.print(f"[green]✔[/] Cache removido ({fmt_bytes(tamanho)} liberados): {pasta}")


@app.command("demo")
def cmd_demo(
    pasta: Annotated[Path, typer.Argument(help="Pasta do ambiente de demonstração.")] = Path(
        "demo"
    ),
    colaboradores: Annotated[int, typer.Option(help="Tamanho do quadro fictício.")] = 500,
    meses: Annotated[int, typer.Option(help="Meses de histórico do quadro.")] = 3,
) -> None:
    """Cria um ambiente com [bold]dados fictícios[/] para testar sem tocar nas bases reais."""
    from gente_etl.demo import criar_ambiente_demo

    with console.status("Gerando planilhas fictícias…"):
        cfg = criar_ambiente_demo(pasta, colaboradores=colaboradores, meses=meses)
    console.print(f"[green]✔[/] Ambiente criado em [bold]{cfg.parent}[/]")
    console.print(f"\nExperimente:\n  [bold]gente-etl executar -c {cfg} -r 2026-08 --detalhes[/]")
