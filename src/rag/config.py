"""Configurações do projeto: caminhos, nomes de tabelas e parâmetros do chunking."""

import os
from pathlib import Path

# Raiz do projeto (este arquivo fica em src/rag/config.py).
RAIZ = Path(__file__).resolve().parents[2]

PASTA_FONTES = RAIZ / "data" / "fontes"
ARQUIVO_ACL = RAIZ / "data" / "acl.yaml"

PASTA_WAREHOUSE = RAIZ / "warehouse"
ARQUIVO_CATALOGO = PASTA_WAREHOUSE / "catalogo.db"

# Caminho RELATIVO de propósito. No Windows, o PyIceberg interpreta "C:\..." como se
# "C:" fosse um protocolo (igual a "s3:") e falha. O teste scripts/teste_iceberg.py
# mostrou que o caminho relativo funciona, desde que o programa rode a partir da raiz.
WAREHOUSE_RELATIVO = "warehouse"

NAMESPACE = "rag"

# Chunking: um chunk por artigo de lei; artigos maiores que o limite são divididos.
CHUNK_MAX_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 50


# Embeddings via Ollama local.
OLLAMA_URL = "http://localhost:11434"
MODELO_EMBEDDING = "bge-m3"        # multilíngue, vetores de 1.024 números
LOTE_EMBEDDING = 16                # textos por chamada ao Ollama

# Busca
TOP_K = 5


def ir_para_raiz() -> None:
    """Garante que o programa rode a partir da raiz (necessário para o caminho relativo)."""
    os.chdir(RAIZ)
