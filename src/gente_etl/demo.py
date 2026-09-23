"""Gera bases FICTÍCIAS no formato das planilhas do RM para testar o pipeline.

Usado pelos testes (pytest) e pelo comando ``gente-etl demo``. Nada aqui é dado real.

A simulação é coerente: o quadro de cada mês = quadro anterior + admitidos − demitidos,
então a reconciliação fecha. Alguns "defeitos" típicos são inseridos de propósito
(cabeçalhos diferentes entre arquivos, datas em texto, campos em branco, uma data
inválida) para exercitar as tratativas comuns.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

import polars as pl
import xlsxwriter

PRIMEIROS = [
    "Ana",
    "Bruno",
    "Carla",
    "Diego",
    "Elisa",
    "Fábio",
    "Gabriela",
    "Hugo",
    "Isabela",
    "João",
    "Karina",
    "Lucas",
    "Mariana",
    "Nicolas",
    "Olívia",
    "Paulo",
    "Renata",
    "Sérgio",
    "Tatiane",
    "Vitor",
]
SOBRENOMES = [
    "Silva",
    "Souza",
    "Oliveira",
    "Santos",
    "Lima",
    "Pereira",
    "Costa",
    "Almeida",
    "Ribeiro",
    "Carvalho",
    "Gomes",
    "Martins",
    "Araújo",
    "Rocha",
]
DIRETORIAS = {
    "Operações": ["Produção", "Logística", "Qualidade"],
    "Comercial": ["Vendas", "Marketing"],
    "Corporativo": ["Gente", "Financeiro", "TI"],
}
CARGOS = ["Analista", "Assistente", "Coordenador", "Especialista", "Operador", "Técnico"]
TIPOS_DEMISSAO = ["Pedido de demissão", "Sem justa causa", "Término de contrato"]
FORNECEDORES = ["Alfa Serviços", "Beta Facilities", "Gama Tech", "Delta Limpeza"]


@dataclass
class Colaborador:
    chapa: str
    nome: str
    cpf: str
    nascimento: date
    genero: str
    diretoria: str
    gerencia: str
    cc: str
    cargo: str
    gestor: str
    admissao: date


def _novo(rng: random.Random, seq: int, admissao: date) -> Colaborador:
    diretoria = rng.choice(list(DIRETORIAS))
    gerencia = rng.choice(DIRETORIAS[diretoria])
    return Colaborador(
        chapa=f"{seq:06d}",
        nome=f"{rng.choice(PRIMEIROS)} {rng.choice(SOBRENOMES)}",
        cpf=f"{rng.randrange(10**9, 10**11):011d}",
        nascimento=date(rng.randint(1965, 2004), rng.randint(1, 12), rng.randint(1, 28)),
        genero=rng.choice("MF"),
        diretoria=diretoria,
        gerencia=gerencia,
        cc=f"CC{sum(map(ord, diretoria + gerencia)) % 900 + 100}",
        cargo=rng.choice(CARGOS),
        gestor=f"{rng.choice(PRIMEIROS)} {rng.choice(SOBRENOMES)}",
        admissao=admissao,
    )


def _dia(rng: random.Random, mes: date) -> date:
    return mes + timedelta(days=rng.randint(0, 27))


def _mes(ano: int, mes: int) -> date:
    return date(ano + (mes - 1) // 12, (mes - 1) % 12 + 1, 1)


def gerar_dados(
    pasta: Path,
    colaboradores: int = 500,
    meses: int = 3,
    ano: int = 2026,
    mes_inicial: int = 6,
    semente: int = 42,
) -> dict[str, list[Path]]:
    """Gera XLSX fictícios em ``pasta/<fluxo>/``. Retorna os arquivos criados por fluxo."""
    rng = random.Random(semente)
    pasta = Path(pasta)
    arquivos: dict[str, list[Path]] = {}
    seq = 1

    ativos: dict[str, Colaborador] = {}
    for _ in range(colaboradores):
        c = _novo(
            rng, seq, date(rng.randint(2005, ano - 1), rng.randint(1, 12), rng.randint(1, 28))
        )
        ativos[c.chapa] = c
        seq += 1

    # foto do fechamento: (mês, [(colaborador, situação)]); demitidos do mês aparecem com "D"
    fotos: list[tuple[date, list[tuple[Colaborador, str]]]] = []
    admitidos: list[tuple[Colaborador, date]] = []
    demitidos: list[tuple[Colaborador, date, str]] = []
    movimentos: list[dict] = []

    for i in range(meses):
        mes = _mes(ano, mes_inicial + i)
        saidas_mes: list[Colaborador] = []
        if i > 0:
            saem = rng.sample(sorted(ativos), k=max(1, len(ativos) // 40))
            for chapa in saem:
                c = ativos.pop(chapa)
                saidas_mes.append(c)
                demitidos.append((c, _dia(rng, mes), rng.choice(TIPOS_DEMISSAO)))
            for _ in range(max(1, colaboradores // 30)):
                c = _novo(rng, seq, _dia(rng, mes))
                ativos[c.chapa] = c
                admitidos.append((c, c.admissao))
                seq += 1
            for chapa in rng.sample(sorted(ativos), k=max(1, len(ativos) // 50)):
                c = ativos[chapa]
                nova_ger = rng.choice(DIRETORIAS[c.diretoria])
                novo_cc = f"CC{sum(map(ord, c.diretoria + nova_ger)) % 900 + 100}"
                movimentos.append(
                    {
                        "CHAPA": chapa,
                        "DATA_MOVIMENTACAO": _dia(rng, mes),
                        "TIPO_MOVIMENTACAO": "Transferência",
                        "SECAO_ANTERIOR": c.cc,
                        "CC_PARA": novo_cc,
                        "FUNCAO_ANTERIOR": c.cargo,
                        "FUNCAO_NOVA": c.cargo,
                    }
                )
                c.gerencia, c.cc = nova_ger, novo_cc
        fotos.append(
            (
                mes,
                [(Colaborador(**vars(c)), "A") for c in ativos.values()]
                + [(Colaborador(**vars(c)), "D") for c in saidas_mes],
            )
        )

    # ---- Quadro: meses anteriores empilhados num arquivo do ano ("Quadro 2026.xlsx");
    #      o último mês num arquivo mensal ("Quadro MM-AAAA.xlsx") SEM coluna de competência
    #      (período vem do nome), com cabeçalhos "amigáveis" e datas em texto.
    def _linhas(mes: date, pessoas: list[tuple[Colaborador, str]], texto: bool) -> list[dict]:
        return [
            {
                "COMPETENCIA": mes,
                "CODCOLIGADA": "1",
                "CODFILIAL": "01",
                "CHAPA": p.chapa,
                "NOME": p.nome,
                "CPF": p.cpf.lstrip("0") if texto else p.cpf,  # Excel "come" zeros
                "DTNASCIMENTO": p.nascimento.strftime("%d/%m/%Y") if texto else p.nascimento,
                "SEXO": p.genero,
                "DIRETORIA": p.diretoria,
                "GERENCIA": p.gerencia,
                "CODSECAO": p.cc,
                "DESCRICAO_SECAO": f"{p.diretoria} - {p.gerencia}",
                "FUNCAO": p.cargo,
                "GESTOR": p.gestor,
                "CODSITUACAO": situacao,
                "CODTIPO": "N",
                "DATAADMISSAO": p.admissao.strftime("%d/%m/%Y") if texto else p.admissao,
            }
            for p, situacao in pessoas
        ]

    arquivos["quadro"] = []
    anteriores = [linha for mes, pessoas in fotos[:-1] for linha in _linhas(mes, pessoas, False)]
    if anteriores:
        arquivos["quadro"].append(
            _gravar(pl.DataFrame(anteriores), pasta / "quadro" / f"Quadro {ano}.xlsx")
        )
    mes, pessoas = fotos[-1]
    df = pl.DataFrame(_linhas(mes, pessoas, True)).drop("COMPETENCIA")
    # uma data de nascimento inválida -> AVISO de falha de conversão
    df = df.with_columns(
        pl.when(pl.int_range(pl.len()) == 0)
        .then(pl.lit("31/02/1990"))
        .otherwise(pl.col("DTNASCIMENTO"))
        .alias("DTNASCIMENTO")
    ).rename({"DATAADMISSAO": "Data de Admissão", "NOME": " Nome ", "CHAPA": "Chapa"})
    arquivos["quadro"].append(_gravar(df, pasta / "quadro" / f"Quadro {mes:%m-%Y}.xlsx"))

    # ---- Admitidos: sem várias colunas (virão do quadro); alguns CC em branco
    adm = pl.DataFrame(
        [
            {
                "CHAPA": c.chapa,
                "NOME": c.nome,
                "DATAADMISSAO": d.strftime("%d/%m/%Y"),
                "CODSECAO": None if j % 7 == 0 else c.cc,
                "FUNCAO": c.cargo,
            }
            for j, (c, d) in enumerate(admitidos)
        ]
    )
    arquivos["admitidos"] = [_gravar(adm, pasta / "admitidos" / "admitidos.xlsx")]

    # ---- Demitidos: só chapa, data e motivo; o resto vem do quadro
    dem = pl.DataFrame(
        [
            {"CHAPA": c.chapa, "DATADEMISSAO": d, "TIPODEMISSAO": t, "MOTIVO": "Motivo fictício"}
            for c, d, t in demitidos
        ]
    )
    arquivos["demitidos"] = [_gravar(dem, pasta / "demitidos" / "demitidos.xlsx")]

    arquivos["movimentacoes"] = [
        _gravar(pl.DataFrame(movimentos), pasta / "movimentacoes" / "movimentacoes.xlsx")
    ]

    # ---- Terceiros: foto do último mês
    ultimo_mes = fotos[-1][0]
    terc = pl.DataFrame(
        [
            {
                "COMPETENCIA": ultimo_mes,
                "CPF": f"{rng.randrange(10**9, 10**11):011d}",
                "NOME": f"{rng.choice(PRIMEIROS)} {rng.choice(SOBRENOMES)}",
                "FORNECEDOR": rng.choice(FORNECEDORES),
                "CODSECAO": f"CC{rng.randint(100, 999)}",
                "FUNCAO": rng.choice(CARGOS),
            }
            for _ in range(max(5, colaboradores // 10))
        ]
    )
    arquivos["terceiros"] = [_gravar(terc, pasta / "terceiros" / "terceiros.xlsx")]

    # ---- Orçado: 12 meses, uma linha por HC; um arquivo com uma aba por semestre
    base_orc = [c for c, _ in fotos[0][1]]
    linhas_orc = []
    for m in range(1, 13):
        for k, p in enumerate(base_orc):
            linhas_orc.append(
                {
                    "PERIODO": date(ano, m, 1),
                    "ID_VAGA": f"V{k:05d}",
                    "CODSECAO": p.cc,
                    "DIRETORIA": p.diretoria,
                    "GERENCIA": p.gerencia,
                    "FUNCAO": p.cargo,
                }
            )
    orc = pl.DataFrame(linhas_orc)
    destino = pasta / "orcado" / f"orcado_{ano}.xlsx"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with xlsxwriter.Workbook(destino) as wb:
        orc.filter(pl.col("PERIODO").dt.month() <= 6).write_excel(wb, worksheet="1S")
        orc.filter(pl.col("PERIODO").dt.month() > 6).write_excel(wb, worksheet="2S")
    arquivos["orcado"] = [destino]
    return arquivos


def _gravar(df: pl.DataFrame, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    df.write_excel(destino, worksheet="Base")
    return destino


def modelo(nome: str) -> str:
    """Conteúdo de um arquivo modelo empacotado (``config.toml`` ou ``esquema.toml``)."""
    return resources.files("gente_etl.modelos").joinpath(nome).read_text(encoding="utf-8")


def criar_ambiente_demo(pasta: Path, colaboradores: int = 500, meses: int = 3) -> Path:
    """Gera dados fictícios + config.toml/esquema.toml apontando para eles."""
    pasta = Path(pasta).resolve()
    gerar_dados(pasta / "dados", colaboradores=colaboradores, meses=meses)
    (pasta / "esquema.toml").write_text(modelo("esquema.toml"), encoding="utf-8")
    config = modelo("config.toml").replace("%OneDriveCommercial%/Gente - Bases/Saida", "saida")
    for fluxo, subpasta in [
        ("Quadro", "quadro"),
        ("Admitidos", "admitidos"),
        ("Demitidos", "demitidos"),
        ("Movimentacoes", "movimentacoes"),
        ("Terceiros", "terceiros"),
        ("Orcado", "orcado"),
    ]:
        config = config.replace(
            f"%OneDriveCommercial%/Gente - Bases/{fluxo}/", f"dados/{subpasta}/"
        )
    config = config.replace('pasta_cache = "../.cache"', 'pasta_cache = ".cache"')
    destino = pasta / "config.toml"
    destino.write_text(config, encoding="utf-8")
    return destino
