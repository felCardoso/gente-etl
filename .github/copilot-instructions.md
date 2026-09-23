# Instruções para o Copilot: gente-etl

Projeto Python de ETL das bases de Gente (RH): quadro, admitidos, demitidos,
movimentações, terceiros e orçado. Substitui a lógica dos dataflows do Power Query.

**Antes de alterar fluxos, leia `docs/GUIA_COPILOT.md`.** Regras essenciais:

- Polars **lazy e vetorizado**. Proibido `pandas`, `map_elements`, `apply` e loops por linha.
  Não chame `.collect()` dentro de `transformar`.
- Use os **nomes internos** das colunas (`matricula`, `centro_custo`...). Cabeçalho novo
  vira alias em `config/esquema.toml`; coluna nova de saída vira `[[coluna]]` no esquema.
- Reutilize `src/gente_etl/comum/limpeza.py` antes de escrever tratativa nova.
- Lógica específica fica em `src/gente_etl/fluxos/<fluxo>.py`, no método `transformar`.
- Não esconda problema de dado (`fill_null` genérico, dedupe silenciosa): crie regra em
  `src/gente_etl/validacao/regras.py`.
- Parâmetros de negócio ficam em `config.toml` (`[fluxos.X.parametros]`), não fixos no código.
- Toda regra nova precisa de teste em `tests/`, com dados **fictícios** construídos no teste.
- **Nunca** coloque dados reais (nomes, CPF, matrículas) em código, teste ou documentação.
- Código, comentários e mensagens em **português**.
- Antes de concluir: `pytest` e `ruff check src tests && ruff format src tests`.
- Regras de negócio descobertas vão para `docs/REGRAS_NEGOCIO.md`.
