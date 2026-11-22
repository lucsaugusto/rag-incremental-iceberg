"""Comandos do projeto.

    uv run python -m rag ingerir                       # lê os documentos e grava documentos_raw e kb_chunks
    uv run python -m rag embeddings                    # gera os vetores dos chunks pendentes
    uv run python -m rag buscar "pergunta" --grupos publico
    uv run python -m rag status                        # mostra o que está nas tabelas

Por enquanto, 'ingerir' reescreve as tabelas inteiras e volta todos os chunks para
"pendente" (os vetores são gerados de novo). A etapa 4 resolve isso: só o que mudou.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.compute as pc

from . import config
from .busca import buscar
from .catalogo import SCHEMA_DOCUMENTOS_RAW, SCHEMA_KB_CHUNKS, abrir_catalogo, criar_tabelas, tabela
from .chunking import gerar_chunks
from .embeddings import ErroOllama, gerar_embeddings, texto_para_embedding
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


def embeddings() -> None:
    catalogo = abrir_catalogo()
    criar_tabelas(catalogo)
    kb = tabela(catalogo, "kb_chunks")
    titulos = {
        d["doc_id"]: d["titulo"]
        for d in tabela(catalogo, "documentos_raw").scan(selected_fields=("doc_id", "titulo")).to_arrow().to_pylist()
    }
    linhas = kb.scan().to_arrow().to_pylist()

    # pendente = ainda sem vetor, ou com vetor de OUTRO modelo (vetores de modelos diferentes não se comparam)
    pendentes = [
        c for c in linhas
        if c["status_embedding"] != "ok" or c["modelo_embedding"] != config.MODELO_EMBEDDING
    ]
    if not pendentes:
        print(f"Nada a fazer: os {len(linhas)} chunks já têm vetor do modelo '{config.MODELO_EMBEDDING}'.")
        return

    print(f"Gerando embeddings de {len(pendentes)} chunks com '{config.MODELO_EMBEDDING}', "
          f"em lotes de {config.LOTE_EMBEDDING}...")
    inicio = time.perf_counter()
    agora = datetime.now(timezone.utc)
    feitos, dimensao, erro = 0, None, None
    for i in range(0, len(pendentes), config.LOTE_EMBEDDING):
        lote = pendentes[i:i + config.LOTE_EMBEDDING]
        entradas = [texto_para_embedding(titulos.get(c["doc_id"], c["doc_id"]), c["secao"], c["texto"]) for c in lote]
        try:
            vetores = gerar_embeddings(entradas)
        except ErroOllama as e:
            erro = e
            break
        for chunk, vetor in zip(lote, vetores):
            chunk.update({
                "embedding": vetor,
                "modelo_embedding": config.MODELO_EMBEDDING,
                "status_embedding": "ok",
                "atualizado_em": agora,
            })
        dimensao = len(vetores[0])
        feitos += len(lote)
        print(f"\r  {feitos}/{len(pendentes)} chunks ({time.perf_counter() - inicio:.0f} s)", end="", flush=True)

    # Os chunks alterados são os mesmos objetos de 'linhas', então a tabela inteira é regravada.
    # O que não foi processado continua "pendente": rodar de novo continua de onde parou.
    if feitos:
        kb.overwrite(pa.Table.from_pylist(linhas, schema=SCHEMA_KB_CHUNKS))
    print()
    if erro:
        print(f"ERRO: {erro}")
        print(f"Gravei os {feitos} chunks já processados; rode o comando de novo para continuar.")
        return
    print(f"Pronto: {feitos} vetores de {dimensao} números gravados em {time.perf_counter() - inicio:.0f} s.")


def cmd_buscar(pergunta: str, grupos: list[str], k: int) -> None:
    catalogo = abrir_catalogo()
    criar_tabelas(catalogo)
    campos = ("chunk_id", "secao", "texto", "acl", "embedding", "modelo_embedding")
    chunks = tabela(catalogo, "kb_chunks").scan(selected_fields=campos).to_arrow().to_pylist()

    com_vetor = [c for c in chunks if c["embedding"]]
    if not com_vetor:
        print("Nenhum chunk tem vetor ainda. Rode antes: uv run python -m rag embeddings")
        return
    modelos = {c["modelo_embedding"] for c in com_vetor}
    if modelos != {config.MODELO_EMBEDDING}:
        print(f"AVISO: há vetores de modelos diferentes {modelos}; rode 'embeddings' para padronizar.")

    vetor = gerar_embeddings([pergunta])[0]     # a pergunta vira vetor com o MESMO modelo
    resultado, bloqueados = buscar(vetor, com_vetor, grupos, k)

    print(f"Pergunta: {pergunta}")
    print(f"Grupos do usuário: {grupos}  |  chunks bloqueados pela ACL: {bloqueados} de {len(com_vetor)}\n")
    if not resultado:
        print("Nenhum chunk visível para esses grupos.")
    for posicao, c in enumerate(resultado, start=1):
        trecho = c["texto"][:220].replace("\n", " ")
        print(f"{posicao}. score {c['score']:.3f}  [{c['chunk_id']}]  acl={c['acl']}")
        print(f"   {c['secao']}")
        print(f"   {trecho}...\n")


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

        print("\n== Embeddings")
        por_status = chunks.group_by(["status_embedding", "modelo_embedding"]).aggregate([("chunk_id", "count")])
        for r in por_status.to_pylist():
            print(f"  status={r['status_embedding']:9s} modelo={r['modelo_embedding']}  chunks={r['chunk_id_count']}")

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
    sub.add_parser("ingerir", help="lê os documentos e grava documentos_raw e kb_chunks")
    sub.add_parser("embeddings", help="gera os vetores dos chunks pendentes")
    p_buscar = sub.add_parser("buscar", help="busca os chunks mais parecidos com a pergunta")
    p_buscar.add_argument("pergunta")
    p_buscar.add_argument("--grupos", nargs="+", default=["publico"], help="grupos do usuário (ACL)")
    p_buscar.add_argument("--k", type=int, default=config.TOP_K, help="quantos resultados mostrar")
    sub.add_parser("status", help="mostra o conteúdo das tabelas")
    args = parser.parse_args()

    if args.comando == "buscar":
        try:
            cmd_buscar(args.pergunta, args.grupos, args.k)
        except ErroOllama as e:
            print(f"ERRO: {e}")
        return
    {"ingerir": ingerir, "embeddings": embeddings, "status": status}[args.comando]()


if __name__ == "__main__":
    main()
