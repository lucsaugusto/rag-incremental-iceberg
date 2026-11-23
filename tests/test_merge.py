from rag.merge import planejar_chunks, planejar_documentos


def doc(hash_arquivo, acl):
    return {"hash_arquivo": hash_arquivo, "acl": acl}


def test_planejar_documentos_classifica_cada_caso():
    gravados = {
        "cdc": doc("h1", ["publico"]),
        "lgpd": doc("h2", ["publico", "juridico"]),
        "pix": doc("h3", ["atendimento"]),
        "antigo": doc("h4", ["publico"]),
    }
    lidos = {
        "cdc": doc("h1", ["publico"]),                  # igual
        "lgpd": doc("h2", ["juridico", "publico"]),     # igual (ordem da acl não importa)
        "pix": doc("h3", ["atendimento", "juridico"]),  # só a acl mudou
        "novo": doc("h5", ["publico"]),                 # novo
    }
    plano = planejar_documentos(gravados, lidos)
    assert plano.iguais == ["cdc", "lgpd"]
    assert plano.so_acl == ["pix"]
    assert plano.novos == ["novo"]
    assert plano.removidos == ["antigo"]
    assert plano.a_processar == ["novo", "pix"]


def test_documento_alterado_pelo_hash():
    plano = planejar_documentos({"cdc": doc("h1", ["publico"])}, {"cdc": doc("h1-editado", ["publico"])})
    assert plano.alterados == ["cdc"]


def chunk(chunk_id, hash_conteudo, acl=("publico",), secao="Art. 1", ordem=1, **extra):
    return {"chunk_id": chunk_id, "hash_conteudo": hash_conteudo, "acl": list(acl), "secao": secao, "ordem": ordem, **extra}


VETOR = {"embedding": [0.1, 0.2], "modelo_embedding": "bge-m3", "status_embedding": "ok"}


def test_so_o_chunk_editado_perde_o_vetor():
    existentes = {
        "cdc#art-48": chunk("cdc#art-48", "a", ordem=1, **VETOR),
        "cdc#art-49": chunk("cdc#art-49", "b", ordem=2, **VETOR),
        "cdc#art-50": chunk("cdc#art-50", "c", ordem=3, **VETOR),
    }
    novos = [
        chunk("cdc#art-48", "a", ordem=1),
        chunk("cdc#art-49", "b-editado", ordem=2),   # prazo de 7 para 14 dias, por exemplo
        chunk("cdc#art-50", "c", ordem=3),
    ]
    plano = planejar_chunks(existentes, novos)
    assert plano.mantidos == 2
    assert [c["chunk_id"] for c in plano.atualizar_texto] == ["cdc#art-49"]
    assert plano.atualizar_texto[0]["status_embedding"] == "pendente"
    assert plano.atualizar_texto[0]["embedding"] is None
    assert plano.inserir == [] and plano.remover == [] and plano.atualizar_meta == []


def test_mudanca_de_acl_mantem_o_vetor():
    existentes = {"pix#art-8": chunk("pix#art-8", "x", acl=["atendimento"], **VETOR)}
    novos = [chunk("pix#art-8", "x", acl=["atendimento", "juridico"])]
    plano = planejar_chunks(existentes, novos)
    assert len(plano.atualizar_meta) == 1
    atualizado = plano.atualizar_meta[0]
    assert atualizado["acl"] == ["atendimento", "juridico"]
    assert atualizado["embedding"] == [0.1, 0.2] and atualizado["status_embedding"] == "ok"
    assert plano.atualizar_texto == []


def test_chunk_novo_e_chunk_removido():
    existentes = {"cdc#art-50": chunk("cdc#art-50", "c", **VETOR)}
    novos = [chunk("cdc#art-50-a", "novo")]
    plano = planejar_chunks(existentes, novos)
    assert [c["chunk_id"] for c in plano.inserir] == ["cdc#art-50-a"]
    assert plano.remover == ["cdc#art-50"]
    assert plano.ids_para_apagar == ["cdc#art-50"]


def test_artigo_inserido_desloca_ordem_mas_nao_gera_embedding():
    # um artigo novo antes muda a "ordem" dos seguintes, mas o texto deles é o mesmo
    existentes = {"cdc#art-49": chunk("cdc#art-49", "b", ordem=2, **VETOR)}
    novos = [chunk("cdc#art-48-a", "novo", ordem=2), chunk("cdc#art-49", "b", ordem=3)]
    plano = planejar_chunks(existentes, novos)
    assert [c["chunk_id"] for c in plano.inserir] == ["cdc#art-48-a"]
    assert [c["chunk_id"] for c in plano.atualizar_meta] == ["cdc#art-49"]
    assert plano.atualizar_meta[0]["embedding"] == [0.1, 0.2]   # 1 embedding novo, não 2
