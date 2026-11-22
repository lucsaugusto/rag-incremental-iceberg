"""Catálogo Iceberg local (SQLite) e o schema das tabelas.

Em produção, o catálogo seria o AWS Glue Data Catalog (ou um catálogo REST) e os arquivos
ficariam no S3. A lógica das tabelas é a mesma.
"""

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.table import Table

from . import config


def _lista(tipo: pa.DataType) -> pa.DataType:
    # O Iceberg chama o item de uma lista de "element"; usar o mesmo nome evita conversões.
    return pa.list_(pa.field("element", tipo))


TIMESTAMP_UTC = pa.timestamp("us", tz="UTC")

# Bronze: um arquivo por linha, como chegou da fonte.
SCHEMA_DOCUMENTOS_RAW = pa.schema([
    ("doc_id", pa.string()),
    ("arquivo", pa.string()),
    ("titulo", pa.string()),
    ("hash_arquivo", pa.string()),
    ("tamanho_bytes", pa.int64()),
    ("paginas", pa.int32()),
    ("texto", pa.string()),            # texto extraído do PDF, antes da limpeza
    ("acl", _lista(pa.string())),
    ("lido_em", TIMESTAMP_UTC),
])

# Gold: um chunk por linha. É a fonte da verdade do RAG.
SCHEMA_KB_CHUNKS = pa.schema([
    ("chunk_id", pa.string()),
    ("doc_id", pa.string()),
    ("secao", pa.string()),
    ("ordem", pa.int32()),
    ("texto", pa.string()),
    ("tokens", pa.int32()),
    ("hash_conteudo", pa.string()),
    ("embedding", _lista(pa.float32())),   # preenchido na etapa 3
    ("modelo_embedding", pa.string()),     # preenchido na etapa 3
    ("status_embedding", pa.string()),     # "pendente" até a etapa 3
    ("acl", _lista(pa.string())),
    ("fonte_arquivo", pa.string()),
    ("atualizado_em", TIMESTAMP_UTC),
])

TABELAS = {
    "documentos_raw": SCHEMA_DOCUMENTOS_RAW,
    "kb_chunks": SCHEMA_KB_CHUNKS,
}


def abrir_catalogo() -> SqlCatalog:
    config.ir_para_raiz()
    config.PASTA_WAREHOUSE.mkdir(exist_ok=True)
    return SqlCatalog(
        "rag_local",
        uri=f"sqlite:///{config.ARQUIVO_CATALOGO.as_posix()}",
        warehouse=config.WAREHOUSE_RELATIVO,
    )


def criar_tabelas(catalogo: SqlCatalog) -> None:
    catalogo.create_namespace_if_not_exists(config.NAMESPACE)
    for nome, schema in TABELAS.items():
        catalogo.create_table_if_not_exists(f"{config.NAMESPACE}.{nome}", schema=schema)


def tabela(catalogo: SqlCatalog, nome: str) -> Table:
    return catalogo.load_table(f"{config.NAMESPACE}.{nome}")
