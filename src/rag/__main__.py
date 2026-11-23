"""Comandos do projeto.

    uv run python -m rag run                           # pipeline completo: ingerir + embeddings
    uv run python -m rag ingerir                       # incremental: só o que mudou na fonte
    uv run python -m rag embeddings                    # gera os vetores dos chunks pendentes
    uv run python -m rag buscar "pergunta" --grupos publico
    uv run python -m rag status                        # conteúdo das tabelas e histórico de snapshots

Toda gravação é incremental e atômica: as linhas que saem e as que entram vão numa única
transação do Iceberg (o equivalente ao MERGE do Spark). Rodar duas vezes sem mudar a fonte
não grava nada.
"""

import argparse
import sys
import time
from datetime import datetime, timezone

from functools import reduce

import pyarrow as pa
import pyarrow.compute as pc
from pyiceberg.expressions import BooleanExpression, In, Or
from pyiceberg.table import Table

from . import config
from .busca import buscar
from .catalogo import SCHEMA_DOCUMENTOS_RAW, SCHEMA_KB_CHUNKS, abrir_catalogo, criar_tabelas, tabela
from .chunking import gerar_chunks
from .embeddings import ErroOllama, gerar_embeddings, texto_para_embedding
from .ingestao import ler_documentos
from .merge import PlanoChunks, planejar_chunks, planejar_documentos


# ---------------------------------------------------------------- helpers de leitura/gravação

def _ler(tab: Table, campos: tuple[str, ...] | None = None, filtro: BooleanExpression | None = None) -> list[dict]:
    argumentos: dict = {}
    if campos:
        argumentos["selected_fields"] = campos
    if filtro is not None:
        argumentos["row_filter"] = filtro
    return tab.scan(**argumentos).to_arrow().to_pylist()


def _em(coluna: str, valores: list[str]) -> BooleanExpression | None:
    return In(coluna, set(valores)) if valores else None


def _ou(*filtros: BooleanExpression | None) -> BooleanExpression | None:
    presentes = [f for f in filtros if f is not None]
    return reduce(Or, presentes) if presentes else None


def _gravar(tab: Table, schema: pa.Schema, apagar: BooleanExpression | None, linhas: list[dict], operacao: str) -> None:
    """Apaga e insere numa única transação: quem lê a tabela vê o antes ou o depois, nunca o meio."""
    if apagar is None and not linhas:
        return
    propriedades = {"rag.operacao": operacao}
    with tab.transaction() as tx:
        if apagar is not None:
            tx.delete(apagar, snapshot_properties=propriedades)
        if linhas:
            tx.append(pa.Table.from_pylist(linhas, schema=schema), snapshot_properties=propriedades)


def ingerir() -> None:
    catalogo = abrir_catalogo()
    criar_tabelas(catalogo)
    raw, kb = tabela(catalogo, "documentos_raw"), tabela(catalogo, "kb_chunks")

    lidos, avisos = ler_documentos()
    if not lidos:
        print(f"Nenhum arquivo .pdf ou .txt encontrado em {config.PASTA_FONTES}")
        return

    # 1. Documentos: compara o hash do arquivo inteiro com o que está gravado
    gravados = {d["doc_id"]: d for d in _ler(raw, ("doc_id", "hash_arquivo", "acl"))}
    plano_docs = planejar_documentos(gravados, {d["doc_id"]: d for d in lidos})

    # 2. Chunks: só os documentos novos/alterados/com acl alterada passam pelo chunking
    existentes: dict[str, dict[str, dict]] = {}
    for c in _ler(kb, filtro=_em("doc_id", plano_docs.a_processar)) if plano_docs.a_processar else []:
        existentes.setdefault(c["doc_id"], {})[c["chunk_id"]] = c

    agora = datetime.now(timezone.utc)
    plano = PlanoChunks()
    for doc in lidos:
        if doc["doc_id"] not in plano_docs.a_processar:
            continue
        chunks = gerar_chunks(doc["doc_id"], doc["texto"], config.CHUNK_MAX_TOKENS, config.CHUNK_OVERLAP_TOKENS)
        for chunk in chunks:
            chunk.update({"acl": doc["acl"], "fonte_arquivo": doc["arquivo"], "atualizado_em": agora})
        plano.juntar(planejar_chunks(existentes.get(doc["doc_id"], {}), chunks))

    removidos_por_doc = len(_ler(kb, ("chunk_id",), _em("doc_id", plano_docs.removidos))) if plano_docs.removidos else 0

    # 3. Gravação: primeiro os chunks, depois os documentos. Se o processo cair no meio,
    #    o documento continua com o hash antigo e é reprocessado na próxima execução.
    _gravar(
        kb, SCHEMA_KB_CHUNKS,
        _ou(_em("chunk_id", plano.ids_para_apagar), _em("doc_id", plano_docs.removidos)),
        plano.linhas_para_gravar, "ingerir",
    )
    _gravar(
        raw, SCHEMA_DOCUMENTOS_RAW,
        _em("doc_id", plano_docs.alterados + plano_docs.so_acl + plano_docs.removidos),
        [d for d in lidos if d["doc_id"] in plano_docs.a_processar], "ingerir",
    )

    # 4. Relatório
    print("== Documentos")
    for rotulo, lista in [("novos", plano_docs.novos), ("alterados", plano_docs.alterados),
                          ("só ACL", plano_docs.so_acl), ("removidos", plano_docs.removidos),
                          ("iguais", plano_docs.iguais)]:
        print(f"  {rotulo:10s} {len(lista):3d}  {', '.join(lista)}")
    print("\n== Chunks")
    print(f"  inseridos                          {len(plano.inserir):5d}")
    print(f"  texto alterado (vetor descartado)  {len(plano.atualizar_texto):5d}")
    print(f"  só metadados (vetor mantido)       {len(plano.atualizar_meta):5d}")
    print(f"  removidos                          {len(plano.remover) + removidos_por_doc:5d}")
    print(f"  sem mudança                        {plano.mantidos:5d}")
    pendentes = len(plano.inserir) + len(plano.atualizar_texto)
    chunks_mudaram = bool(plano.linhas_para_gravar or plano.ids_para_apagar or plano_docs.removidos)
    if not plano_docs.a_processar and not plano_docs.removidos:
        print("\nNada mudou na fonte: nenhuma gravação feita.")
    elif not chunks_mudaram:
        print("\nOs arquivos mudaram, mas nenhum chunk mudou (ex.: só quebras de linha): "
              "atualizado apenas o hash em documentos_raw.")
    else:
        print(f"\nPendentes de embedding: {pendentes}")
    for aviso in avisos:
        print(f"  AVISO: {aviso}")


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

    # Grava só os chunks processados (apaga a versão pendente e insere a com vetor).
    # O que não foi processado continua "pendente": rodar de novo continua de onde parou.
    processados = pendentes[:feitos]
    if processados:
        _gravar(kb, SCHEMA_KB_CHUNKS, _em("chunk_id", [c["chunk_id"] for c in processados]), processados, "embeddings")
    print()
    if erro:
        print(f"ERRO: {erro}")
        print(f"Gravei os {feitos} chunks já processados; rode o comando de novo para continuar.")
        return
    print(f"Pronto: {feitos} vetores de {dimensao} números gravados em {time.perf_counter() - inicio:.0f} s.")


def run() -> None:
    """Pipeline completo, na ordem de uma DAG: ingerir -> embeddings."""
    print("######## 1/2 ingerir\n")
    ingerir()
    print("\n######## 2/2 embeddings\n")
    embeddings()


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

    print(f"== Histórico de rag.kb_chunks ({len(kb.metadata.snapshots)} snapshots; últimos 8)")
    print(f"  {'quando':15s} {'operação':10s} {'etapa':11s} {'+linhas':>8s} {'-linhas':>8s} {'total':>7s}")
    for snap in kb.metadata.snapshots[-8:]:
        resumo = snap.summary
        props = (getattr(resumo, "additional_properties", None) or {}) if resumo else {}
        operacao = resumo.operation.value if resumo else "?"
        quando = datetime.fromtimestamp(snap.timestamp_ms / 1000).strftime("%d/%m %H:%M:%S")
        print(f"  {quando:15s} {operacao:10s} {props.get('rag.operacao', '-'):11s} "
              f"{props.get('added-records', '0'):>8s} {props.get('deleted-records', '0'):>8s} {props.get('total-records', '?'):>7s}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m rag")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("run", help="pipeline completo: ingerir + embeddings")
    sub.add_parser("ingerir", help="incremental: grava só o que mudou na fonte")
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
    {"run": run, "ingerir": ingerir, "embeddings": embeddings, "status": status}[args.comando]()


if __name__ == "__main__":
    main()
