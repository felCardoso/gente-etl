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
| Período | Primeiro dia do mês de referência da linha (`periodo`). | ✅ |
| Data de referência da foto | _Em que dia o RM é extraído? O quadro de "agosto" é a foto de 31/08 ou da data de extração?_ | ❓ |
| Matrícula (`matricula`) | Chave do colaborador no RM (CHAPA). _É única entre coligadas ou a chave é coligada + chapa?_ | ❓ |
| Turnover | _Fórmula usada nos BIs (fica no modelo semântico, mas vale registrar)._ | ❓ |

## 1. Quadro

- **Origem:** _relatório/consulta do RM, quem extrai, onde é salvo, com que frequência._
- **Granularidade:** 1 linha por colaborador por período.
- **Chave:** `periodo` + `matricula`.

| # | Regra | Onde (código) | Status |
|---|---|---|---|
| Q1 | `periodo` vem da coluna da planilha; sem ela, usa o mês de referência da execução | `fluxos/quadro.py: definir_periodo_foto` | 🚧 |
| Q2 | `tempo_empresa_meses` = meses completos entre admissão e fim do período | `fluxos/quadro.py` | 🚧 |
| Q3 | _Ex.: situações que saem do HC (afastado INSS, licença...)_ | | ❓ |
| Q4 | _Ex.: de-para de centro de custo para diretoria/gerência_ | | ❓ |

## 2. Admitidos

- **Granularidade:** 1 linha por admissão.
- **Chave:** `matricula` + `data_admissao`.
- **Enriquecimento:** colunas ausentes ou vazias são completadas com o **registro mais
  recente do quadro** da mesma matrícula (o valor da própria base tem prioridade).

| # | Regra | Onde | Status |
|---|---|---|---|
| A1 | `periodo` = mês da `data_admissao` | `fluxos/admitidos.py` | 🚧 |
| A2 | Completa com o quadro (exceto `periodo` e `tempo_empresa_meses`) | `config.toml: [fluxos.admitidos.enriquecer]` | 🚧 |
| A3 | _Readmissão: mesma matrícula volta? Conta como admissão?_ | | ❓ |
| A4 | _Transferência entre coligadas conta como admissão?_ | | ❓ |

## 3. Demitidos

- **Granularidade:** 1 linha por desligamento.
- **Chave:** `matricula` + `data_demissao`.
- **Enriquecimento:** igual a admitidos. _Confirmar: o demitido ainda aparece no quadro
  do mês anterior? Se o quadro só tiver ativos, a cobertura do enriquecimento cai._

| # | Regra | Onde | Status |
|---|---|---|---|
| D1 | `periodo` = mês da `data_demissao` | `fluxos/demitidos.py` | 🚧 |
| D2 | `tempo_empresa_meses` = meses completos entre admissão e demissão | `fluxos/demitidos.py` | 🚧 |
| D3 | _Classificação voluntário/involuntário a partir de `tipo_demissao`_ | | ❓ |

## 4. Movimentações

- **Granularidade:** 1 linha por movimentação.
- **Colunas "de/para":** `centro_custo_anterior` → `centro_custo`; `cargo_anterior` → `cargo`.

| # | Regra | Onde | Status |
|---|---|---|---|
| M1 | `periodo` = mês da `data_movimentacao` | `fluxos/movimentacoes.py` | 🚧 |
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
| O1 | `periodo` precisa ser dia 1 (ERRO se não for) | `validacao/regras.py: periodos_orcado` | ✅ |
| O2 | Cada ano deve ter 12 meses (AVISO) | idem | ✅ |
| O3 | _Revisão semestral substitui o ano todo ou só o 2º semestre?_ | | ❓ |

## 7. Base consolidada

- Todas as bases empilhadas no layout de `config/esquema.toml`, mais a coluna `base`
  (QUADRO, ADMITIDOS, DEMITIDOS, MOVIMENTACOES, TERCEIROS, ORCADO).
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
| `reconciliacao` | AVISO | quadro(t) ≠ quadro(t−1) + admitidos(t) − demitidos(t) |
| `periodo_nao_inicio_mes` | ERRO | orçado com período fora do dia 1 |
| `orcado_meses` | AVISO | ano do orçado sem 12 meses |
| `variacao_volume` | AVISO | linhas por base variaram além do limite vs. última execução |
| `coluna_fora_esquema` | AVISO | fluxo criou coluna que não está no esquema |
| `contrato_saida` | ERRO | base final diferente do esquema |

_Reconciliação: confirmar a convenção de datas (item 0). Se a foto do quadro é tirada
antes do fim do mês, admitidos e demitidos do fim do mês podem cair no período seguinte._
