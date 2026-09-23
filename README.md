# gente-etl

ETL **local** das bases de Gente (**quadro, admitidos, demitidos, movimentações,
terceiros e orçado**) em Python + [Polars](https://pola.rs). Tira dos dataflows do
Power Platform a lógica demorada e cara (merges, colunas calculadas, tipagem) que
consome capacidade do Fabric, e gera **uma base consolidada em Parquet e CSV** para
um dataflow "fino" ler.

```
XLSX no SharePoint ──► gente-etl (seu notebook) ──► base_gente.parquet/.csv ──► dataflow fino ──► BIs
```

- ⚡ **Rápido**: leitura de XLSX com calamine (Rust), processamento lazy e vetorizado,
  **cache**: XLSX que não mudou não é relido (o orçado semestral quase nunca é relido).
- 🧩 **Modular**: tratativas comuns em um lugar, uma classe por fluxo, esquema de
  colunas em TOML.
- ✅ **Validado**: chaves, obrigatórias, conversões, reconciliação
  `quadro(t−1) + admitidos − demitidos = quadro(t)`, contrato de saída, variação de volume.
  Erros **bloqueiam a gravação**, e a base publicada nunca fica pela metade.
- 🖥️ **CLI** com progresso, tabelas e relatório (Typer + Rich).
- 🔒 **Nada de dado no git**: `.gitignore` bloqueia xlsx/csv/parquet e o `config.toml` local.

## Performance

Medido com 900 mil linhas de quadro (3 XLSX de 22 MB), 4 vCPUs:

| Execução | Tempo | Pico de RAM |
|---|---|---|
| 1ª (lendo os XLSX) | ~10 s | ~1,8 GB |
| seguintes (cache) | ~4 s | ~1,2 GB |

A leitura do XLSX é a etapa dominante, por isso o cache faz tanta diferença.

## Instalação

Requer **Python 3.11+**. Com [uv](https://docs.astral.sh/uv/) (recomendado):

```powershell
git clone <repo> gente-etl
cd gente-etl
uv sync --extra dev          # cria .venv e instala tudo
uv run gente-etl --help
```

Ou com pip:

```powershell
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
gente-etl --help
```

## Primeiros passos

### 1. Teste com dados fictícios

```powershell
gente-etl demo                                    # cria ./demo com planilhas fictícias
gente-etl executar -c demo/config.toml -r 2026-08 --detalhes
```

### 2. Configure para as bases reais

1. No SharePoint, sincronize as pastas das bases com o OneDrive ("Sincronizar" ou
   "Adicionar atalho a Meus arquivos").
2. Crie sua configuração local:

   ```powershell
   gente-etl config iniciar      # cria config/config.toml (seu, fora do git) e config/esquema.toml
   ```

3. Edite `config/config.toml` com os caminhos (aceita `%OneDriveCommercial%` e padrões `*.xlsx`).
4. Descubra os cabeçalhos das planilhas e ajuste os `aliases` em `config/esquema.toml`:

   ```powershell
   gente-etl inspecionar "C:/Users/voce/OneDrive - Empresa/Gente - Bases/Quadro/quadro.xlsx"
   ```

5. Confira e rode:

   ```powershell
   gente-etl config verificar
   gente-etl executar --simular   # processa e valida sem gravar
   gente-etl executar
   ```

## Comandos

| Comando | O que faz |
|---|---|
| `gente-etl executar [FLUXOS...]` | Roda o ETL. Sem argumentos, roda todos os ativos. Dependências (quadro) entram sozinhas. |
| `  -r, --referencia 2026-08` | Mês de referência (padrão: hoje). Usado quando a planilha não tem período. |
| `  -s, --simular` | Processa e valida, mas não grava a base. |
| `  --sem-cache` | Relê todos os XLSX. |
| `  --forcar` | Grava mesmo com ERRO de validação. |
| `  -d, --detalhes` | Mostra cada arquivo lido (e se veio do cache) e as validações OK. |
| `gente-etl fluxos` | Tabela com fluxos, arquivos encontrados, tamanho, data de modificação e cache. |
| `gente-etl inspecionar ARQ.xlsx` | Abas, cabeçalhos e a qual coluna do esquema cada um corresponde. |
| `gente-etl config iniciar / verificar` | Cria / valida a configuração e confere os caminhos. |
| `gente-etl cache limpar` | Apaga o cache de leitura. |
| `gente-etl demo [PASTA]` | Ambiente de teste com dados fictícios. |

Código de saída: `0` sucesso · `1` gravação bloqueada por ERRO · `2` erro de configuração/leitura.

## Configuração

Dois arquivos em `config/`:

- **`esquema.toml`** (versionado) é o **contrato** da base final. Define a ordem, o nome
  interno, o nome de saída (`saida`, igual ao que os modelos semânticos esperam), o
  tipo e os `aliases` de cada coluna. Os aliases são comparados sem acento, sem
  maiúsculas e sem pontuação, então "Data de Admissão" casa com `DATA_ADMISSAO`.
- **`config.toml`** (local, fora do git) guarda os caminhos, as opções de saída e as
  validações e, para cada fluxo, arquivos, aba, chave, obrigatórias, enriquecimento
  pelo quadro e parâmetros. O modelo comentado está em
  [`config/config.exemplo.toml`](config/config.exemplo.toml).

## Como funciona

Cada fluxo segue `extrair → transformar → validar`:

1. **Extrair** (comum a todos): lê cada arquivo/aba **como texto** (com cache Parquet
   por arquivo), renomeia os cabeçalhos pelos aliases **antes de empilhar**, faz trim,
   converte vazio em nulo, remove linhas vazias e aplica os tipos do esquema. As datas
   são aceitas como data real, texto `dd/mm/aaaa`/`aaaa-mm-dd` ou serial do Excel; os
   números como `1.234,56`, `R$`, `%`.
2. **Transformar** (específico): regras de negócio de cada base em
   `src/gente_etl/fluxos/<fluxo>.py`. O quadro é a foto do fechamento de cada mês; os
   arquivos podem ser de um ano, de vários anos ou de um mês, e o período vem da coluna de
   competência ou do nome do arquivo (`Quadro 05-2026`). Admitidos e movimentações são
   **completados com o quadro do mesmo mês pela CHAVE M** (`dd/MM/yyyy_matricula`);
   demitidos, que saem do quadro no mês da demissão, pela **CHAVE M-1** (foto do mês
   anterior). Colunas ausentes ou vazias recebem o valor do quadro, e o valor da própria
   base tem prioridade.
3. **Validar**: regras genéricas e específicas. Depois tudo é **empilhado** no layout do
   esquema, com a coluna `base` indicando a origem, validado de novo e gravado de forma
   atômica. O relatório (`relatorio.json` + CSVs de amostra de cada problema) fica em
   `<pasta_saida>/relatorios/`.

```
src/gente_etl/
  cli.py              interface (Typer + Rich)
  pipeline.py         orquestração, consolidação, histórico
  config.py           modelos pydantic dos TOMLs
  io/excel.py         leitura XLSX + cache + paralelismo
  io/saida.py         gravação Parquet/CSV atômica
  comum/limpeza.py    tratativas comuns (com equivalências M)
  fluxos/             base.py + um módulo por base
  validacao/          regras e resultado
  demo.py             gerador de dados fictícios
  modelos/            modelos de config.toml e esquema.toml
```

## Desenvolvimento

```powershell
uv run pytest                                  # testes (dados fictícios)
uv run ruff check src tests; uv run ruff format src tests
```

A migração da lógica dos dataflows será feita com o Copilot (Claude Opus) dentro da
empresa. Documentação de apoio:

- [`docs/GUIA_COPILOT.md`](docs/GUIA_COPILOT.md): roteiro de migração fluxo a fluxo,
  regras de código, cola M → Polars, modelos de prompt e checklist.
- [`docs/REGRAS_NEGOCIO.md`](docs/REGRAS_NEGOCIO.md): modelo para documentar as regras de
  cada base (para pessoas e para a IA da empresa).
- [`docs/DATAFLOW_FABRIC.md`](docs/DATAFLOW_FABRIC.md): dataflow fino no Fabric e como
  trocar o motor sem alterar os modelos semânticos.
- [`.github/copilot-instructions.md`](.github/copilot-instructions.md): instruções que o
  Copilot carrega automaticamente.

## Segurança dos dados (LGPD)

- Planilhas, CSV, Parquet, cache, relatórios e `config/config.toml` estão no `.gitignore`.
- O cache (`.cache/`) e os relatórios contêm dados pessoais: ficam só na sua máquina ou
  na pasta de saída do SharePoint, com as permissões dela.
- Testes e demo usam somente dados gerados (`gente_etl.demo`).
