# Guia para IA (GitHub Copilot / Claude Opus): como desenvolver os fluxos do gente-etl

> **Para quem é este documento:** para o assistente de IA que vai migrar a lógica dos
> dataflows do Power Query (M) para este projeto, dentro do ambiente da empresa, com
> acesso ao código M real e aos dados reais. Também serve para a pessoa que conduz a
> migração acompanhar o processo.
>
> **Como usar:** abra o chat do Copilot no VS Code, escolha o modelo Claude Opus, anexe
> este arquivo (`#file:docs/GUIA_COPILOT.md`) e siga a seção [Roteiro](#5-roteiro-de-migração-um-fluxo-por-vez).
> Faça **um fluxo por vez** e rode os testes a cada passo.

---

## 1. Contexto do negócio

- Área de Gente (RH). Seis bases extraídas manualmente do **TOTVS RM** para planilhas
  **XLSX no SharePoint**:

  | Fluxo           | Atualização | Observação |
  |-----------------|-------------|------------|
  | `quadro`        | semanal     | Headcount, **foto do fechamento de cada mês**. Base de referência para completar as outras |
  | `admitidos`     | semanal     | Completado com colunas/valores do quadro |
  | `demitidos`     | semanal     | Completado com colunas/valores do quadro |
  | `movimentacoes` | mensal      | Transferências, promoções etc. |
  | `terceiros`     | semanal     | Prestadores, sem vínculo com o quadro |
  | `orcado`        | semestral   | 1 linha = 1 HC orçado; ano inteiro; `periodo` = 1º dia de cada mês |

- Hoje a lógica roda em **dataflows do Power Platform/Fabric** e consome capacidade (CU).
  O objetivo é fazer **toda a transformação pesada localmente** com este programa e deixar
  no Fabric um dataflow "fino" que só lê o arquivo pronto (ver `docs/DATAFLOW_FABRIC.md`).
- Regra da companhia: **os BIs só podem ter dataflows como fonte** (nunca SharePoint
  direto). Por isso a saída continua passando por um dataflow.
- Cerca de **8 modelos semânticos** e fluxos de outras áreas consomem essas bases:
  **o layout de saída (nomes, tipos e ordem das colunas) não pode mudar**.
- Volume: **mais de 2,5 milhões de linhas** somando as bases. Performance é prioridade.
- Dados pessoais (CPF, nome etc.): **LGPD**. Nenhum dado real vai para o git.
- **`periodo` é sempre o 1º dia do mês.** A **CHAVE M** (`dd/MM/yyyy_matricula`, ex.
  `01/05/2026_000123`) liga admitidos e movimentações à foto do quadro **do mesmo mês**
  (`lp.chave_m`, coluna `chave_m`/`CHAVE M`).
- **Demitidos saem do quadro** no mês da demissão: o merge deles usa a **CHAVE M-1**,
  a foto do **mês anterior** (`lp.chave_m_anterior`, coluna `chave_m_1`/`CHAVE M-1`,
  `enriquecer.chave_quadro = ["chave_m"]`). Na base final, `base` = `Demitidos`.
- Os arquivos do quadro podem ser de um ano (`Quadro 2020`), de vários anos
  (`Quadro 2018-2019`) ou de um mês (`Quadro 05-2026`). O período vem da coluna de
  competência ou do nome do arquivo; mês repetido entre arquivos usa o mais recente.

## 2. Arquitetura (onde cada coisa mora)

```
config/
  esquema.toml             CONTRATO da base final: colunas, tipos, nomes de saída, aliases
  config.toml              caminhos locais e parâmetros (cada pessoa tem o seu; fora do git)
src/gente_etl/
  config.py                leitura/validação dos TOMLs (pydantic)
  io/excel.py              leitura XLSX (calamine) como texto + cache Parquet + paralelismo
  io/saida.py              gravação atômica de Parquet/CSV
  comum/limpeza.py         TRATATIVAS COMUNS (reutilize antes de escrever código novo)
  fluxos/base.py           classe Fluxo: extrair -> transformar -> validar
  fluxos/<fluxo>.py        LÓGICA ESPECÍFICA de cada base  <- aqui entra o M migrado
  validacao/regras.py      regras de validação automáticas
  pipeline.py              orquestra: dependências, consolidação, validação, gravação
  cli.py                   interface (Typer + Rich)
  demo.py                  gerador de dados fictícios (usado nos testes)
tests/                     pytest; sempre com dados fictícios
```

Ciclo de cada fluxo (`fluxos/base.py`):

1. **`ler()`**: lê todos os arquivos/abas do fluxo como texto, com cache, e **renomeia os
   cabeçalhos para os nomes internos** usando os `aliases` do `esquema.toml`, arquivo
   por arquivo, antes de empilhar.
2. **`extrair()`**: limpa textos (trim, vazio vira nulo), remove linhas vazias e converte
   os tipos do esquema (datas em qualquer formato, número com vírgula etc.).
3. **`transformar(lf)`**: **regras de negócio**. É o único método que normalmente precisa
   ser escrito. Recebe e devolve `pl.LazyFrame` com os **nomes internos** das colunas.
4. **`validar(df)`**: obrigatórias, chave única, datas plausíveis, falhas de conversão e
   cobertura do enriquecimento. Pode ser estendido com regras do fluxo.

Depois o `pipeline` empilha tudo no layout do esquema, com a coluna `base` indicando a
origem, valida o consolidado (contrato de saída, reconciliação do quadro, variação de
volume) e grava CSV e Parquet.

## 3. Regras obrigatórias de código

1. **Polars, sempre vetorizado.** Proibido: `pandas`, `map_elements`, `apply`,
   `iter_rows` em dados, loops Python por linha. Se algo parece exigir loop, use
   `pl.when/then`, `join`, `over()` (janela), `replace_strict` ou um dicionário via join.
2. **Lazy até o fim.** Em `transformar`, trabalhe com `pl.LazyFrame` e não chame
   `.collect()`. O pipeline coleta uma vez só.
3. **Use os nomes internos** (`matricula`, `centro_custo`, ...), nunca os cabeçalhos do
   RM. Cabeçalho novo = **alias** no `esquema.toml`, não código.
4. **Coluna nova na saída = linha nova no `esquema.toml`** (com `tipo` e, se preciso,
   `saida`). Coluna criada no fluxo que não estiver no esquema é descartada com AVISO.
   O tipo produzido tem que bater com o do esquema (o pipeline acusa erro se não bater).
5. **Reutilize `comum/limpeza.py`.** Se uma tratativa servir para mais de um fluxo, ela
   vai para lá, com teste. Funções disponíveis:

   | Função | Para quê | Equivalente M |
   |---|---|---|
   | `renomear_por_aliases` | cabeçalhos da origem para nomes internos | `Table.RenameColumns` |
   | `limpar_texto(s)` | trim, espaços duplos, vazio vira nulo | `Text.Trim`, `Text.Clean` |
   | `remover_acentos` | remove acentos (vetorizado) | `Text.Remove`/replace |
   | `para_data` | texto (vários formatos) ou serial Excel para data | `Date.From` |
   | `para_decimal`/`para_inteiro` | "1.234,56", "R$", "%" | `Number.From` |
   | `para_booleano` | Sim/Não/S/N/1/0/X | `Logical.From` |
   | `converter_tipos` | aplica os tipos do esquema | `Table.TransformColumnTypes` |
   | `inicio_mes` | 1º dia do mês | `Date.StartOfMonth` |
   | `chave_m` | CHAVE M `dd/MM/yyyy_matricula` | `Date.ToText(..., "dd/MM/yyyy") & "_" & ...` |
   | `chave_m_anterior` | CHAVE M-1 (mês anterior) | `Date.AddMonths([periodo], -1)` + CHAVE M |
   | `periodo_do_nome_arquivo` | `MM-AAAA` do nome do arquivo | `Text.BetweenDelimiters` no `Source.Name` |
   | `meses_entre` | meses completos entre datas | `DATEDIF(...,"M")` |
   | `ultimo_por_chave` | registro mais recente por chave | `Table.Sort` + `Table.Distinct` |
   | `completar_com_referencia` | merge que completa colunas vazias/ausentes | `Table.NestedJoin` + `ExpandTableColumn` + `if null` |
   | `garantir_colunas` | cria colunas ausentes e ordena pelo esquema | `Table.SelectColumns(..., MissingField.UseNull)` |

6. **Não esconda problema de dado.** Não use `fill_null` genérico, `strict=False` sem
   motivo ou deduplicação silenciosa. Se o dado está errado, crie uma **regra de
   validação** (AVISO ou ERRO) com amostra das linhas.
7. **Parâmetros de negócio vão para o `config.toml`** (`[fluxos.X.parametros]`), não
   ficam fixos no código (ex.: lista de situações que contam como HC).
8. **Toda regra nova precisa de teste** em `tests/`, com `DataFrame` pequeno e fictício
   montado no próprio teste. Nunca use arquivos reais nos testes.
9. **Comentários e nomes em português**, no estilo do código existente. Marque com
   `# M:` o passo do Power Query equivalente quando ajudar a rastrear a migração.
10. **Nada de dado real no repositório**, nem em comentário, teste ou exemplo.

## 4. Tradução M para Polars (cola rápida)

| Power Query (M) | Polars |
|---|---|
| `Table.SelectRows(t, each [x] = "A")` | `lf.filter(pl.col("x") == "A")` |
| `each List.Contains({"A","B"}, [x])` | `pl.col("x").is_in(["A", "B"])` |
| `Table.AddColumn(t, "n", each if [a] > 0 then "P" else "N")` | `lf.with_columns(pl.when(pl.col("a") > 0).then(pl.lit("P")).otherwise(pl.lit("N")).alias("n"))` |
| `Table.ReplaceValue(t, "x", "y", Replacer.ReplaceText, {"c"})` | `pl.col("c").str.replace_all("x", "y", literal=True)` |
| `Table.ReplaceValue(..., Replacer.ReplaceValue, ...)` | `pl.col("c").replace({"x": "y"})` |
| `Table.NestedJoin(a, "k", b, "k", "b", JoinKind.LeftOuter)` + `Expand` | `a.join(b.select("k", "col1"), on="k", how="left")` |
| `JoinKind.Inner` / `LeftAnti` | `how="inner"` / `how="anti"` |
| Merge para buscar **1** valor (PROCV) | `join` com `b.unique("k")` antes, para **não duplicar linhas** |
| `Table.Group(t, {"k"}, {{"n", each Table.RowCount(_)}})` | `lf.group_by("k").agg(pl.len().alias("n"))` |
| Coluna agregada sem perder linhas | `pl.col("v").sum().over("k")` |
| `Table.Distinct(t, {"k"})` | `lf.unique(subset=["k"], keep="first", maintain_order=True)` |
| `Table.Sort(t, {{"d", Order.Descending}})` | `lf.sort("d", descending=True)` |
| `Table.Combine({a, b})` | `pl.concat([a, b], how="diagonal_relaxed")` |
| `Table.UnpivotOtherColumns(t, {"k"}, "Atributo", "Valor")` | `lf.unpivot(index=["k"], variable_name="atributo", value_name="valor")` |
| `Table.Pivot` | `df.pivot(...)` (exige `collect`; evite, ou use `group_by` + `agg`) |
| `Table.FillDown(t, {"c"})` | `pl.col("c").forward_fill()` (garanta a ordem antes) |
| `Date.StartOfMonth([d])` / `Date.EndOfMonth` | `pl.col("d").dt.month_start()` / `.dt.month_end()` |
| `Date.AddMonths([d], 1)` | `pl.col("d").dt.offset_by("1mo")` |
| `Date.Year([d])`, `Date.Month([d])` | `.dt.year()`, `.dt.month()` |
| `Duration.Days([f] - [i])` | `(pl.col("f") - pl.col("i")).dt.total_days()` |
| `Text.Start([t], 3)` / `Text.End` | `.str.slice(0, 3)` / `.str.tail(3)` |
| `Text.Upper`, `Text.Proper` | `.str.to_uppercase()`, `.str.to_titlecase()` |
| `Text.Contains([t], "x")` | `.str.contains("x", literal=True)` |
| `Text.PadStart([t], 6, "0")` | `.str.zfill(6)` |
| `Text.Split` + expandir | `.str.split_exact(";", n).struct.unnest()` |
| `try ... otherwise null` | cast/parse com `strict=False` **+ regra de validação** para as falhas |
| Tabela de-para em outra consulta | um `pl.DataFrame` de mapeamento e `join` (ou `replace_strict`) |

**Armadilhas conhecidas:**

- Merge no M com chave duplicada no lado direito **multiplica linhas**. Em Polars também.
  Deduplique a referência (`ultimo_por_chave`) antes do `join` e valide contagens.
- Merge no M ignora maiúsculas e minúsculas só com `Comparer.OrdinalIgnoreCase`; Polars
  sempre diferencia. Normalize as duas chaves (`.str.to_uppercase()`) se o M fazia isso.
- Chave numérica vs. texto: `"000123"` ≠ `"123"`. Use `zeros_esquerda` no esquema.
- `null` em comparações: `pl.col("x") != "A"` é `null` quando `x` é nulo, e a linha
  **sai** do `filter`. Se o M mantinha a linha, use `pl.col("x").ne_missing("A")`.
- Datas no Excel podem vir como número serial, texto `dd/mm/aaaa` ou data real.
  `para_data` já trata os três casos.

## 5. Roteiro de migração (um fluxo por vez)

Ordem sugerida: **quadro → admitidos → demitidos → movimentações → terceiros → orçado**
(o quadro alimenta os outros).

### Passo 0: preparar
- `uv sync --extra dev` (ou `pip install -e ".[dev]"`), depois `pytest` (tudo verde).
- `gente-etl config iniciar` e ajuste de `config/config.toml` para os caminhos do SharePoint sincronizado.

### Passo 1: mapear colunas (sem código)
1. Rode `gente-etl inspecionar "<arquivo>.xlsx"` em cada tipo de planilha.
2. Para cada cabeçalho **sem correspondência** que é usado no M, adicione um alias à
   coluna existente ou crie uma coluna nova no `esquema.toml`.
3. Ajuste `saida` de cada coluna para o **nome exato** que o modelo semântico usa hoje.
   A ordem das `[[coluna]]` define a ordem na base final.

### Passo 2: levantar o que o M faz
Cole o código M do dataflow no chat e peça:

> "Liste, em ordem, cada passo deste M, classificando em: (a) já coberto pelas tratativas
> comuns do gente-etl (renomear, trim, tipos, linhas vazias), (b) regra de negócio a
> migrar para `fluxos/<fluxo>.py`, (c) validação/limpeza de dado, (d) passo
> desnecessário. Para os itens (b), diga quais colunas usam e quais criam."

Registre as regras de negócio descobertas em `docs/REGRAS_NEGOCIO.md`.

### Passo 3: escrever o teste antes
Em `tests/test_fluxo_<fluxo>.py`, monte um `pl.LazyFrame` pequeno com casos que
exercitam cada regra, **inclusive os casos de borda** (nulos, duplicados, datas nas
viradas de mês), e o resultado esperado. Exemplo:

```python
from datetime import date
import polars as pl
from gente_etl.fluxos.base import Contexto
from gente_etl.fluxos.quadro import Quadro

def test_quadro_exclui_afastados(cfg):  # fixture `cfg` = ambiente demo (tests/conftest.py)
    cfg.fluxos["quadro"].parametros["situacoes_fora_hc"] = ["AFASTADO"]
    fluxo = Quadro(Contexto(config=cfg, referencia=date(2026, 8, 1)))
    entrada = pl.LazyFrame({
        "matricula": ["1", "2"], "situacao": ["ATIVO", "AFASTADO"],
        "data_admissao": [date(2020, 1, 1)] * 2,
    })
    saida = fluxo.transformar(entrada).collect()
    assert saida["matricula"].to_list() == ["1"]
```

### Passo 4: implementar em `transformar`
- Escreva no ponto marcado `>>> PONTO DE MIGRAÇÃO` do fluxo.
- Um bloco por regra, com comentário curto e a referência `# M: <passo>`.
- Rode `pytest` e `ruff check src tests && ruff format src tests`.

### Passo 5: conferir contra o dataflow atual (paridade)
1. Exporte a tabela do dataflow atual para CSV/Parquet (mesmo período).
2. `gente-etl executar <fluxo> --simular` e depois compare, em Python:

```python
import polars as pl
novo = pl.read_parquet("saida/base_gente.parquet").filter(pl.col("base") == "QUADRO")
antigo = pl.read_csv("export_dataflow_quadro.csv", separator=";", infer_schema=False)
# 1) mesmas colunas, mesma ordem?  2) mesma contagem por período?
# 3) anti-join pela chave nos dois sentidos  4) diferenças coluna a coluna nas chaves comuns
```

Peça ao Copilot: *"Escreva um script de comparação entre estes dois DataFrames pela chave
`[periodo, matricula]` que mostre linhas só de um lado e, para as comuns, quantas
diferenças há por coluna com exemplos."* Toda diferença precisa ser **explicada** (bug no
novo, bug no antigo ou regra de negócio mal entendida) e registrada.

### Passo 6: validações do fluxo
Se descobriu alguma regra de consistência ("todo demitido precisa ter tipo de
demissão", "CC precisa existir na tabela de CCs"), transforme em regra em
`validacao/regras.py` e chame no `validar()` do fluxo, com teste.

## 6. Como criar uma regra de validação

```python
# validacao/regras.py
def centro_custo_conhecido(df: pl.DataFrame, validos: list[str], escopo: str) -> list[Resultado]:
    fora = df.filter(pl.col("centro_custo").is_not_null() & ~pl.col("centro_custo").is_in(validos))
    if fora.height:
        return [Resultado("cc_desconhecido", escopo, Severidade.AVISO,
                          f"{fora.height} linha(s) com CC fora da lista.", fora.height, fora.head(50))]
    return [ok("cc_conhecido", escopo, "Todos os CCs conhecidos")]

# fluxos/quadro.py
def validar(self, df):
    return super().validar(df) + regras.centro_custo_conhecido(df, [...], self.nome)
```

Severidades: **ERRO** bloqueia a gravação (dado que não pode ir para o BI); **AVISO**
aparece no relatório; **INFO**/OK é só registro.

## 7. Modelos de prompt

**Migrar um passo:**
> "No `gente-etl`, fluxo `fluxos/demitidos.py`, implemente em `transformar` a regra abaixo,
> que no M está assim: ```<trecho M>```. Siga `docs/GUIA_COPILOT.md` (Polars lazy,
> vetorizado, nomes internos, reutilize `comum/limpeza.py`). Escreva primeiro o teste em
> `tests/test_fluxo_demitidos.py` com casos de borda. Se criar coluna de saída, inclua no
> `esquema.toml`."

**Revisar:**
> "Revise o diff contra `docs/GUIA_COPILOT.md` seção 3. Aponte uso de loop por linha,
> `collect()` antecipado, join que pode multiplicar linhas, `fill_null`/`strict=False` que
> esconde erro e regra sem teste."

**Performance:**
> "Este fluxo leva X s com N linhas. Analise o plano com `lf.explain()` e sugira
> otimizações sem mudar o resultado (menos colunas antes do join, filtros antes, evitar
> `collect` intermediário, `over()` no lugar de group_by + join)."

## 8. Checklist de "pronto" por fluxo

- [ ] Todos os cabeçalhos usados estão como alias no `esquema.toml`
- [ ] Nomes de `saida` iguais aos do modelo semântico
- [ ] Todas as regras do M migradas, com `# M:` e teste
- [ ] `pytest` verde e `ruff check`/`ruff format` sem apontamentos
- [ ] Paridade com o dataflow atual conferida e diferenças explicadas
- [ ] Regras de negócio registradas em `docs/REGRAS_NEGOCIO.md`
- [ ] `gente-etl executar` sem ERRO nos dados reais; AVISOs entendidos
- [ ] Tempo de execução anotado (1ª execução e com cache)
