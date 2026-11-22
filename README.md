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
| 3. Embeddings + busca | Vetores e busca por similaridade | a fazer |
| 4. Incremental | Reprocessa só o que mudou; remoções propagadas | a fazer |
| 5. ACL | Mesma pergunta, usuários diferentes, respostas diferentes | a fazer |
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
| Warehouse do Iceberg com caminho relativo | O PyIceberg no Windows interpreta `C:\...` como se `C:` fosse um protocolo e falha. O teste `scripts/teste_iceberg.py` comparou as opções: o caminho relativo funciona, o `file:///C:/...` não |
| ACL vazia = ninguém vê | Padrão seguro: um documento esquecido no `acl.yaml` fica bloqueado, nunca aberto para todos |
| Limpeza do texto antes do chunking | O site do Planalto recua os artigos com espaço "não separável" (`\u00a0`, mais de 3.600 vezes no CDC); sem normalizar, nenhum artigo seria reconhecido. PDFs salvos pelo navegador trazem URL, data e numeração de página, também removidos |

---

## Como rodar

Pré-requisitos: [uv](https://docs.astral.sh/uv/) e [Ollama](https://ollama.com/).

```bash
uv sync                                   # cria o ambiente com Python 3.12
uv run python -m rag ingerir              # lê os PDFs e grava as tabelas Iceberg
uv run python -m rag status               # mostra o conteúdo das tabelas
uv run pytest                             # testes
```

Próximas etapas: `uv run python -m rag perguntar "..." --usuario ana` e `uv run python -m rag avaliar`.

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
