# Integração com o Fabric: dataflow "fino"

A regra da companhia é que os BIs só leiam **dataflows**. Então o caminho fica assim:

```
RM ──(extração manual)──► XLSX no SharePoint
                             │  (OneDrive sincroniza)
                             ▼
                  gente-etl (no notebook)  ← toda a lógica pesada, sem gastar CU
                             │
                             ▼
      base_gente.parquet / .csv na pasta de saída do SharePoint
                             │
                             ▼
     Dataflow "fino" no Fabric: só lê o arquivo e filtra por base
                             │
                             ▼
           Modelos semânticos (os ~8 BIs, sem alteração)
```

## Estratégia para não mexer nos modelos semânticos

Os modelos semânticos apontam para **entidades (tabelas) de dataflows**. A forma mais
segura de trocar o motor sem quebrar nada:

1. **Mantenha os mesmos dataflows e os mesmos nomes de entidade.** Edite cada consulta
   existente e substitua todos os passos pelo código "fino" abaixo, que devolve as
   **mesmas colunas, com os mesmos nomes e tipos**.
2. Ajuste `saida` em `config/esquema.toml` para os nomes que as entidades já usam.
3. Antes de publicar, compare a entidade nova com a antiga (contagens por período e
   amostra de linhas). Veja o Passo 5 do `GUIA_COPILOT.md`.
4. Atualize o dataflow e depois os modelos semânticos normalmente. Nenhuma alteração
   nos BIs deveria ser necessária.

> Se uma entidade antiga tinha colunas diferentes das outras bases, use
> `Table.SelectColumns` no dataflow fino para devolver exatamente a lista original.
> Com `gravar_por_fluxo = true` no `config.toml`, cada base também é gravada num
> arquivo próprio (`base_gente_quadro.parquet` etc.), o que deixa o dataflow ainda mais leve.
> O de terceiros (`base_gente_terceiros.parquet`) traz os três rótulos de terceiros.

## Parquet ou CSV?

Prefira **Parquet**: preserva os tipos (datas, inteiros), ocupa bem menos espaço (no demo,
25× menos que o CSV) e é muito mais rápido de ler. O CSV fica como alternativa e para quem quiser abrir no Excel.

## Consulta M do dataflow fino (Parquet)

```powerquery
let
    // Ajuste o site e o caminho da pasta de saída
    Site    = SharePoint.Contents("https://SUAEMPRESA.sharepoint.com/sites/SEU_SITE", [ApiVersion = 15]),
    Pasta   = Site{[Name = "Documentos Compartilhados"]}[Content]
                  {[Name = "Gente - Bases"]}[Content]
                  {[Name = "Saida"]}[Content],
    Arquivo = Pasta{[Name = "base_gente.parquet"]}[Content],
    Base    = Parquet.Document(Arquivo),

    // Uma entidade por base: troque o filtro em cada consulta
    // Rótulos: QUADRO, ADMITIDOS, DEMITIDOS, ORCADO, MOVIMENTACOES,
    //          QUADRO-TERCEIROS, ADMITIDOS-TERCEIROS, DEMITIDOS-TERCEIROS
    Quadro  = Table.SelectRows(Base, each [BASE] = "QUADRO"),
    SemBase = Table.RemoveColumns(Quadro, {"BASE"})
in
    SemBase
```

- `SharePoint.Contents` navega direto na pasta e é bem mais leve que `SharePoint.Files`,
  que lista o site inteiro.
- Com `gravar_por_fluxo = true`, troque o arquivo por `base_gente_quadro.parquet` e
  remova o filtro.

## Alternativa CSV

```powerquery
let
    Arquivo = /* mesma navegação acima */ Pasta{[Name = "base_gente.csv"]}[Content],
    Csv     = Csv.Document(Arquivo, [Delimiter = ";", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Cab     = Table.PromoteHeaders(Csv, [PromoteAllScalars = true]),
    Tipos   = Table.TransformColumnTypes(Cab, {{"periodo", type date}, {"data_admissao", type date}}, "en-US")
in
    Tipos
```

No CSV as datas saem como `AAAA-MM-DD` e os decimais com vírgula (configurável em `[csv]`).

## Rotina semanal sugerida

1. Extrair as bases do RM e salvar os XLSX nas pastas do SharePoint (como hoje).
2. Esperar o OneDrive sincronizar (ícone verde) e rodar `gente-etl executar`.
3. Conferir o painel final: **0 erros**; avisos entendidos.
4. Esperar o OneDrive subir os arquivos de saída e atualizar o dataflow fino (manual ou
   agendado logo depois do horário combinado).
