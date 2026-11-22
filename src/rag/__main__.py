"""Comandos do projeto.

    uv run python -m rag ingerir   # lê os documentos e grava documentos_raw e kb_chunks
    uv run python -m rag status    # mostra o que está nas tabelas

Nesta etapa (2), a ingestão reescreve as tabelas inteiras a cada execução.
Na etapa 4 ela passa a ser incremental (só o que mudou).
"""

import argparse
import sys
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.compute as pc

from . import config
from .catalogo import SCHEMA_DOCUMENTOS_RAW, SCHEMA_KB_CHUNKS, abrir_catalogo, criar_tabelas, tabela
from .chunking import gerar_chunks
from .ingestao import ler_documentos


def ingerir() -> None:
    catalogo = abrir_catalogo()
    criar_tabelas(catalogo)

    documentos, avisos = ler_documentos()
    if not documentos:
        print(f"Nenhum arquivo .pdf ou .txt encontrado em {config.PASTA_FONTES}")
        return

    agora = datetime.now(timezone.utc)
    chunks = []
    resumo = []
    for doc in documentos:
        estatisticas: dict = {}
        chunks_do_doc = gerar_chunks(
            doc["doc_id"], doc["texto"], config.CHUNK_MAX_TOKENS, config.CHUNK_OVERLAP_TOKENS, estatisticas
        )
        resumo.append((doc["doc_id"], len(chunks_do_doc), estatisticas.get("versoes_antigas_descartadas", 0)))
        for chunk in chunks_do_doc:
            chunk.update({
                "embedding": None,
                "modelo_embedding": None,
                "status_embedding": "pendente",
                "acl": doc["acl"],                  # o chunk herda a permissão do documento
                "fonte_arquivo": doc["arquivo"],
                "atualizado_em": agora,
            })
            chunks.append(chunk)

    tabela(catalogo, "documentos_raw").overwrite(pa.Table.from_pylist(documentos, schema=SCHEMA_DOCUMENTOS_RAW))
    tabela(catalogo, "kb_chunks").overwrite(pa.Table.from_pylist(chunks, schema=SCHEMA_KB_CHUNKS))

    print(f"Documentos lidos: {len(documentos)}  |  chunks gravados: {len(chunks)}\n")
    print(f"  {'doc_id':20s} {'chunks':>7s} {'redações antigas descartadas':>30s}")
    for doc_id, n_chunks, descartadas in resumo:
        print(f"  {doc_id:20s} {n_chunks:7d} {descartadas:30d}")
    for aviso in avisos:
        print(f"  AVISO: {aviso}")
    print("\nRode 'uv run python -m rag status' para ver o resultado.")


def status() -> None:
    catalogo = abrir_catalogo()
    criar_tabelas(catalogo)
    docs = tabela(catalogo, "documentos_raw")
    kb = tabela(catalogo, "kb_chunks")
    chunks = kb.scan().to_arrow()

    print("== rag.documentos_raw")
    for linha in docs.scan(selected_fields=("doc_id", "paginas", "tamanho_bytes", "acl")).to_arrow().to_pylist():
        paginas = f"{linha['paginas']:4d} páginas" if linha["paginas"] else "   texto    "
        print(f"  {linha['doc_id']:20s} {paginas}  {linha['tamanho_bytes'] / 1024:8.0f} KB  acl={linha['acl']}")

    print("\n== rag.kb_chunks (por documento)")
    if chunks.num_rows:
        resumo = chunks.group_by("doc_id").aggregate([
            ("chunk_id", "count"), ("tokens", "mean"), ("tokens", "max"),
        ]).sort_by("doc_id")
        print(f"  {'doc_id':20s} {'chunks':>7s} {'tokens médio':>13s} {'tokens máx':>11s}")
        for r in resumo.to_pylist():
            print(f"  {r['doc_id']:20s} {r['chunk_id_count']:7d} {r['tokens_mean']:13.0f} {r['tokens_max']:11d}")

        print("\n== Exemplos de chunks")
        amostra = chunks.filter(pc.equal(chunks["ordem"], 3)).to_pylist()[:3]
        for c in amostra:
            trecho = c["texto"][:160].replace("\n", " ")
            print(f"  [{c['chunk_id']}] {c['secao']}\n    {trecho}...\n")

    print(f"== Snapshots (histórico Iceberg): documentos_raw={len(docs.metadata.snapshots)}  kb_chunks={len(kb.metadata.snapshots)}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m rag")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("ingerir", help="lê os PDFs e grava documentos_raw e kb_chunks")
    sub.add_parser("status", help="mostra o conteúdo das tabelas")
    args = parser.parse_args()
    {"ingerir": ingerir, "status": status}[args.comando]()


if __name__ == "__main__":
    main()
