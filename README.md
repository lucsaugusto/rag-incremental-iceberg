# rag-incremental-iceberg

Pipeline de dados que mantém uma base de conhecimento para RAG (Retrieval-Augmented Generation) **correta, atualizada, segura e medida**, usando tabelas Apache Iceberg como fonte da verdade.

O foco não é o chatbot. É a engenharia de dados por trás dele:

- **Incremental:** só o que mudou nos documentos passa pelo modelo de embedding (hash de conteúdo + `MERGE`).
- **Seguro:** cada chunk carrega a permissão (ACL) do documento e o filtro é aplicado *antes* da busca.
- **Rastreável:** cada vetor guarda o modelo que o gerou; o Iceberg guarda o histórico (time travel).
- **Medido:** um golden set de perguntas mede a qualidade da busca (recall@5) a cada mudança.

Tudo roda localmente, sem nuvem e sem custo.

---

## Dados

Documentos públicos em português (legislação é de domínio público), salvos como texto a partir dos sites oficiais (o Planalto publica as leis em HTML, não em PDF). A ingestão aceita `.txt` e `.pdf`.

| Documento | Grupo de acesso simulado |
| --- | --- |
| Código de Defesa do Consumidor (Lei 8.078/1990) | `publico` |
| Lei Geral de Proteção de Dados (Lei 13.709/2018) | `publico`, `juridico` |
| Regulamento do Pix (Banco Central) | `atendimento`, `juridico` |

Os grupos são fictícios e servem para demonstrar o controle de acesso.

---

## Arquitetura

```
data/fontes/ (.txt e .pdf)                ← documentos originais
        │  python -m rag run
        ▼
rag.documentos_raw   (Iceberg)            ← um arquivo por linha + hash + acl
        ▼
rag.kb_chunks        (Iceberg)            ← um chunk por linha: texto, vetor, acl, hash, modelo
        ▼
busca por similaridade + filtro de acl    ← só o que o usuário pode ver
        ▼
LLM local responde citando os chunks
```

| Camada | Tabela | Uma linha é |
| --- | --- | --- |
| Bronze | `rag.documentos_raw` | Um arquivo, com caminho, hash do arquivo e ACL |
| Gold | `rag.kb_chunks` | Um chunk, com texto, embedding, ACL, hash do texto e versão do modelo |
| Avaliação | `rag.eval_resultados` | O resultado de uma pergunta do golden set numa execução |

---

## Stack local e equivalente em produção

| Peça | Neste projeto | Em produção (AWS, por exemplo) |
| --- | --- | --- |
| Tabelas | Apache Iceberg via PyIceberg (catálogo SQLite) | Iceberg no S3 + Glue Data Catalog |
| Consultas e MERGE | DuckDB + PyIceberg | Spark no Glue ou EMR |
| Leitura dos documentos | Texto puro e pypdf | Amazon Textract, Docling |
| Embeddings | Modelo pequeno no Ollama | Amazon Bedrock |
| Busca vetorial | DuckDB (similaridade de cosseno) | OpenSearch ou pgvector |
| Geração da resposta | Modelo pequeno no Ollama | Bedrock ou API de um fornecedor |
| Orquestração | `python -m rag run` | Airflow (MWAA) |
| Testes | pytest | pytest + testes de qualidade de dados |

---

## Etapas do projeto

| Etapa | Entrega | Status |
| --- | --- | --- |
| 1. Estrutura | Repositório, ambiente e README | concluída |
| 2. Ingestão + chunking | Documentos → `documentos_raw` e `kb_chunks` (574 chunks) | concluída |
| 3. Embeddings + busca | Vetores com `bge-m3` e busca por similaridade com filtro de ACL | concluída |
| 4. Incremental | Reprocessa só o que mudou; remoções propagadas; gravação atômica | concluída |
| 5. ACL | Mesma pergunta, usuários diferentes, respostas diferentes | concluída (junto com a etapa 3) |
| 6. Resposta com citação | LLM local responde citando os chunks | a fazer |
| 7. Avaliação | Golden set + recall@5 | a fazer |
| 8. Time travel (opcional) | "O que o assistente sabia no dia X?" | a fazer |
| 9. Orquestração (futura) | Mesmas funções orquestradas em Dagster local + DAG para Airflow | a fazer |

---

## Decisões técnicas

| Decisão | Por quê |
| --- | --- |
| Um chunk por artigo de lei (artigos longos divididos com sobreposição) | O artigo é a unidade de sentido da lei, e o `chunk_id` fica estável (`cdc#art-49`): inserir um artigo novo não muda o id dos outros, então a etapa incremental não reprocessa o que não mudou |
| Artigo repetido: fica a última redação | Páginas "compiladas" de normas trazem as redações antigas antes da atual (no Regulamento do Pix, 94 redações antigas). A última é a vigente; ela fica na posição original do artigo. Limitação conhecida: redações antigas de parágrafos dentro de um mesmo artigo ainda não são tratadas |
| Cabeçalho de contexto no texto do embedding | "Art. 49. O consumidor pode desistir..." sozinho não diz de qual lei é. O modelo recebe título do documento + capítulo/seção + texto; a tabela guarda só o texto do chunk |
| Vetor de outro modelo conta como pendente | Vetores de modelos diferentes não são comparáveis; trocar `MODELO_EMBEDDING` faz todos os chunks serem reprocessados |
| Busca por força bruta, filtro de ACL antes do ranking | Com ~600 chunks, comparar com todos leva milissegundos. Filtrar antes evita que trechos proibidos ocupem as primeiras posições e sumam do resultado |
| Incremental em dois níveis: hash do arquivo, depois hash do chunk | Documento igual nem passa pelo chunking. Documento alterado passa, mas só os chunks com texto diferente perdem o vetor. Mudança só de ACL, seção ou ordem mantém o vetor |
| Apagar e inserir na mesma transação Iceberg | É o equivalente ao `MERGE`: quem lê a tabela vê a versão de antes ou a de depois, nunca uma mistura. Cada snapshot leva a propriedade `rag.operacao` para o histórico mostrar qual etapa gravou |
| Gravar os chunks antes dos documentos | Se o processo cair no meio, `documentos_raw` ainda tem o hash antigo e o documento é reprocessado na próxima execução. Como tudo é idempotente, reprocessar não duplica nada |
| Quebras de linha normalizadas antes do hash do chunk + `.gitattributes` com LF | Um `git checkout` no Windows trocou LF por CRLF em todas as linhas do CDC. O hash do arquivo mudou, mas o do chunk não, então nenhum embedding foi gerado à toa. O `.gitattributes` fixa LF para os bytes serem iguais em qualquer sistema |
| Warehouse do Iceberg com caminho relativo | O PyIceberg no Windows interpreta `C:\...` como se `C:` fosse um protocolo e falha. O teste `scripts/teste_iceberg.py` comparou as opções: o caminho relativo funciona, o `file:///C:/...` não |
| ACL vazia = ninguém vê | Padrão seguro: um documento esquecido no `acl.yaml` fica bloqueado, nunca aberto para todos |
| Limpeza do texto antes do chunking | O site do Planalto recua os artigos com espaço "não separável" (`\u00a0`, mais de 3.600 vezes no CDC); sem normalizar, nenhum artigo seria reconhecido. PDFs salvos pelo navegador trazem URL, data e numeração de página, também removidos |

---

## Resultados

### Busca com filtro de ACL (etapa 3)

| Pergunta | Grupos | 1º resultado | Score | Chunks bloqueados |
| --- | --- | --- | --- | --- |
| "posso desistir de uma compra feita pela internet?" | publico | `cdc#art-49` (direito de arrependimento) | 0,692 | 328 de 574 |
| "como funciona o Pix agendado?" | publico | `cdc#art-40` (não responde: o Pix não é visível) | 0,390 | 328 de 574 |
| "como funciona o Pix agendado?" | atendimento | `regulamento-pix#art-8` (Pix Agendado) | 0,750 | 246 de 574 |

A busca 1 acha o artigo certo sem nenhuma palavra em comum: a pergunta diz "compra pela internet" e o artigo diz "contratação fora do estabelecimento comercial". As buscas 2 e 3 são a mesma pergunta com resultados diferentes conforme quem pergunta.

### Incremental (etapa 4)

| Mudança na fonte | Chunks reprocessados | Embeddings gerados | Tempo de embedding |
| --- | --- | --- | --- |
| Nenhuma | 0 | 0 | 0 s |
| Art. 49 do CDC editado (7 → 14 dias) | 1 com texto alterado, 143 sem mudança | **1** | 16 s |
| ACL do CDC alterada | 144 só com metadados (vetor mantido) | **0** | 0 s |
| Reconstrução completa, para comparar | 574 | 574 | alguns minutos |

O incremental tem dois custos: recomputar embeddings (reduzido ao mínimo pelo hash do chunk) e reescrever arquivos Parquet. O PyIceberg apaga linhas em modo copy-on-write: mudar 144 linhas chegou a regravar os 574 registros do arquivo onde elas estavam. Em escala, isso se controla particionando `kb_chunks` por `doc_id` ou usando o modo merge-on-read do Iceberg (Spark, Trino).

---

## Como rodar

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e [Ollama](https://ollama.com/).

```bash
uv sync                                   # cria o ambiente com Python 3.12
ollama pull bge-m3                        # modelo de embedding (uma vez)
uv run python -m rag run                  # pipeline completo: ingerir + embeddings
uv run python -m rag ingerir              # incremental: grava só o que mudou na fonte
uv run python -m rag embeddings           # gera os vetores dos chunks pendentes
uv run python -m rag buscar "posso desistir de uma compra feita pela internet?" --grupos publico
uv run python -m rag status               # mostra o conteúdo das tabelas
uv run pytest                             # testes
```

Próximas etapas: resposta com citação e avaliação com golden set.

---

## Estrutura

```
rag-incremental-iceberg/
├── README.md
├── pyproject.toml
├── data/
│   ├── fontes/              # documentos de entrada (.txt, .pdf)
│   ├── acl.yaml             # permissões simuladas por documento
│   └── golden_set.jsonl     # perguntas e chunks esperados
├── src/rag/
│   ├── __main__.py          # comandos: run, perguntar, avaliar
│   ├── config.py
│   ├── catalogo.py          # criação das tabelas Iceberg
│   ├── ingestao.py          # leitura dos PDFs + hash + acl
│   ├── chunking.py
│   ├── embeddings.py
│   ├── merge.py             # incremental: insere, atualiza, remove
│   ├── busca.py             # similaridade + filtro de acl
│   ├── resposta.py          # LLM com citação
│   └── avaliacao.py
└── tests/
```
