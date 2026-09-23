# Regras de negócio: bases de Gente

> Documento vivo. Registra **o que** cada base significa e **quais regras** a
> transformação aplica, para que pessoas e a IA da empresa entendam os dados sem
> depender de quem escreveu o código.
>
> **Como preencher:** durante a migração de cada dataflow (ver `docs/GUIA_COPILOT.md`,
> passo 2), anote cada regra encontrada no M e confirme com a área de negócio.
> Marque o status: ✅ confirmada · ❓ a confirmar · 🚧 implementada sem confirmação.
>
> ⚠️ **Não coloque dados reais aqui** (nomes, CPFs, matrículas). Use exemplos fictícios.

---

## 0. Definições gerais

| Termo | Definição | Status |
|---|---|---|
| HC (headcount) | _Ex.: colaboradores com vínculo ativo no último dia do período. Afastados contam? Aprendizes/estagiários contam?_ | ❓ |
| Período | **Sempre o 1º dia do mês** (`periodo`). Período fora do dia 1 é ERRO (não é corrigido). | ✅ |
| CHAVE M | `dd/MM/yyyy` do período + `_` + matrícula (ex.: `01/05/2026_000123`). Liga admitidos e movimentações à **foto do quadro do mesmo mês**. Coluna de saída `CHAVE M`. | ✅ |
| CHAVE M-1 | CHAVE M do **mês anterior** (ex.: demissão em 05/2026 → `01/04/2026_000123`). Usada no merge dos demitidos. Coluna de saída `CHAVE M-1`. | ✅ |
| Foto do quadro | Fechamento de cada mês. **O demitido sai do quadro** no mês da demissão. | ✅ |
| Matrícula (`matricula`) | Chave do colaborador no RM (CHAPA). _É única entre coligadas ou a chave é coligada + chapa?_ | ❓ |
| Turnover | _Fórmula usada nos BIs (fica no modelo semântico, mas vale registrar)._ | ❓ |

## 1. Quadro

- **Origem:** _relatório/consulta do RM, quem extrai, onde é salvo, com que frequência._
- **Granularidade:** 1 linha por colaborador por período (foto do fechamento do mês).
- **Chave:** `periodo` + `matricula`.
- **Arquivos:** podem conviver na mesma pasta arquivos empilhados de um ano
  (`Quadro 2020`), de vários anos (`Quadro 2018-2019`) ou de um mês (`Quadro 05-2026`).

| # | Regra | Onde (código) | Status |
|---|---|---|---|
| Q1 | `periodo` vem da coluna de competência; sem ela, de `MM-AAAA` no nome do arquivo; sem os dois, fica vazio (ERRO) | `fluxos/quadro.py: definir_periodo_foto` | ✅ |
| Q2 | Mês presente em mais de um arquivo: vale o arquivo **modificado por último** (AVISO lista os casos). `sobreposicao = "erro"` bloqueia em vez de escolher | `fluxos/quadro.py` | 🚧 |
| Q3 | `chave_m` = CHAVE M do período e matrícula | `fluxos/quadro.py` | ✅ |
| Q4 | `tempo_empresa_meses` = meses completos entre admissão e fim do período | `fluxos/quadro.py` | 🚧 |
| Q5 | Situações fora do HC na reconciliação (padrão: nenhuma, pois demitidos já saem do quadro). _Afastados contam?_ | `config.toml: situacoes_fora_hc` | ❓ |
| Q6 | _Ex.: de-para de centro de custo para diretoria/gerência_ | | ❓ |

## 2. Admitidos

- **Granularidade:** 1 linha por admissão.
- **Chave:** `matricula` + `data_admissao`.
- **Enriquecimento:** colunas ausentes ou vazias são completadas com a **foto do quadro do
  mês da admissão**, pela CHAVE M (o valor da própria base tem prioridade).

| # | Regra | Onde | Status |
|---|---|---|---|
| A1 | `periodo` = mês da `data_admissao` | `fluxos/admitidos.py` | 🚧 |
| A2 | Completa com o quadro pela CHAVE M (exceto `periodo` e `tempo_empresa_meses`) | `config.toml: [fluxos.admitidos.enriquecer]` | ✅ |
| A3 | _Readmissão: mesma matrícula volta? Conta como admissão?_ | | ❓ |
| A4 | _Transferência entre coligadas conta como admissão?_ | | ❓ |

## 3. Demitidos

- **Granularidade:** 1 linha por desligamento.
- **Chave:** `matricula` + `data_demissao`.
- **Enriquecimento:** o demitido **sai do quadro** no mês da demissão, então o merge usa a
  **CHAVE M-1**: a foto do fechamento do **mês anterior** (`chave = ["chave_m_1"]`,
  `chave_quadro = ["chave_m"]`). Quem foi admitido e demitido no mesmo mês não está em
  nenhuma foto: aparece na validação `cobertura_quadro`.
- **Na base consolidada:** as linhas vêm da base de demitidos, com `base` = `Demitidos`.

| # | Regra | Onde | Status |
|---|---|---|---|
| D1 | `periodo` = mês da `data_demissao` | `fluxos/demitidos.py` | 🚧 |
| D1b | `chave_m_1` = CHAVE M-1; merge com o quadro por ela | `fluxos/demitidos.py` + `config.toml` | ✅ |
| D1c | Rótulo na coluna `base` = `Demitidos` | `config.toml: rotulo` | ✅ |
| D2 | `tempo_empresa_meses` = meses completos entre admissão e demissão | `fluxos/demitidos.py` | 🚧 |
| D3 | _Classificação voluntário/involuntário a partir de `tipo_demissao`_ | | ❓ |

## 4. Movimentações

- **Granularidade:** 1 linha por movimentação.
- **Colunas "de/para":** `centro_custo_anterior` → `centro_custo`; `cargo_anterior` → `cargo`.

| # | Regra | Onde | Status |
|---|---|---|---|
| M1 | `periodo` = mês da `data_movimentacao`; enriquecimento pela CHAVE M | `fluxos/movimentacoes.py` | 🚧 |
| M2 | _Quais tipos de movimentação entram (promoção, transferência, mérito...)?_ | | ❓ |

## 5. Terceiros

- **Granularidade:** 1 linha por prestador por período.
- **Chave:** `periodo` + `cpf`.

| # | Regra | Onde | Status |
|---|---|---|---|
| T1 | `periodo` da planilha ou mês de referência | `fluxos/terceiros.py` | 🚧 |

## 6. Orçado

- **Granularidade:** 1 linha = 1 HC orçado por mês. Base anual, revisada semestralmente.
- **Chave:** `periodo` + `id_vaga`.

| # | Regra | Onde | Status |
|---|---|---|---|
| O1 | `periodo` precisa ser dia 1 (ERRO se não for; vale para todas as bases) | `validacao/regras.py: periodo_inicio_mes` | ✅ |
| O2 | Cada ano deve ter 12 meses (AVISO) | idem | ✅ |
| O3 | _Revisão semestral substitui o ano todo ou só o 2º semestre?_ | | ❓ |

## 7. Base consolidada

- Todas as bases empilhadas no layout de `config/esquema.toml`, mais a coluna `base`
  (QUADRO, ADMITIDOS, Demitidos, MOVIMENTACOES, TERCEIROS, ORCADO; cada rótulo é
  configurável em `rotulo`).
- Colunas que não se aplicam a uma base ficam vazias (ex.: `data_demissao` no quadro).

### Validações automáticas

| Regra | Severidade | O que verifica |
|---|---|---|
| `sem_linhas` | ERRO | fluxo sem nenhuma linha |
| `coluna_ausente` / `obrigatoria_nula` | ERRO | colunas de `obrigatorias` inexistentes ou vazias |
| `chave_duplicada` | ERRO | combinação de `chave` repetida |
| `falha_conversao` | AVISO | valor preenchido que não virou data/número |
| `data_implausivel` | AVISO | datas antes de 1950 ou mais de 400 dias após a referência |
| `cobertura_quadro` | AVISO | % de linhas encontradas no quadro abaixo do mínimo |
| `reconciliacao` | AVISO | quadro(t) ≠ quadro(t−1) + admitidos(t) − demitidos(t), sem as `situacoes_fora_hc` |
| `periodo_nao_inicio_mes` | ERRO | período fora do dia 1 (qualquer base) |
| `periodo_em_varios_arquivos` | AVISO | mesmo mês do quadro em 2+ arquivos (usado o mais recente) |
| `orcado_meses` | AVISO | ano do orçado sem 12 meses |
| `variacao_volume` | AVISO | linhas por base variaram além do limite vs. última execução |
| `coluna_fora_esquema` | AVISO | fluxo criou coluna que não está no esquema |
| `contrato_saida` | ERRO | base final diferente do esquema |

_Reconciliação: como o demitido sai do quadro no mês da demissão, a conta fecha sem
ajustes. `situacoes_fora_hc` fica para outras situações que não contam como HC._
