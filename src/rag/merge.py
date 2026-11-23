"""Etapa incremental: decide o que mudou entre a fonte e as tabelas.

São funções puras (sem banco, sem arquivo): recebem o que está gravado e o que foi lido,
e devolvem um plano. Gravar o plano no Iceberg fica no __main__.py. Separar "decidir" de
"gravar" é o que permite testar a lógica incremental sem depender do PyIceberg.

    Documento novo            -> gera todos os chunks (pendentes de embedding)
    Documento alterado        -> chunking de novo; só os chunks com texto diferente perdem o vetor
    Só a ACL mudou            -> chunks mantêm o vetor; atualiza só a coluna acl
    Documento removido        -> apaga todos os chunks dele
    Documento igual           -> não faz nada (nem chunking)
"""

from dataclasses import dataclass, field

CAMPOS_DO_VETOR = ("embedding", "modelo_embedding", "status_embedding")


@dataclass
class PlanoDocumentos:
    novos: list[str] = field(default_factory=list)
    alterados: list[str] = field(default_factory=list)
    so_acl: list[str] = field(default_factory=list)
    removidos: list[str] = field(default_factory=list)
    iguais: list[str] = field(default_factory=list)

    @property
    def a_processar(self) -> list[str]:
        """Documentos que passam pelo chunking nesta execução."""
        return self.novos + self.alterados + self.so_acl


@dataclass
class PlanoChunks:
    inserir: list[dict] = field(default_factory=list)          # chunk novo -> pendente
    atualizar_texto: list[dict] = field(default_factory=list)  # texto mudou -> pendente (vetor descartado)
    atualizar_meta: list[dict] = field(default_factory=list)   # texto igual, acl/seção mudou -> vetor mantido
    remover: list[str] = field(default_factory=list)           # chunk_id que sumiu da fonte
    mantidos: int = 0

    def juntar(self, outro: "PlanoChunks") -> None:
        self.inserir += outro.inserir
        self.atualizar_texto += outro.atualizar_texto
        self.atualizar_meta += outro.atualizar_meta
        self.remover += outro.remover
        self.mantidos += outro.mantidos

    @property
    def linhas_para_gravar(self) -> list[dict]:
        return self.inserir + self.atualizar_texto + self.atualizar_meta

    @property
    def ids_para_apagar(self) -> list[str]:
        """Update no Iceberg = apagar a linha antiga e gravar a nova, na mesma transação."""
        atualizados = [c["chunk_id"] for c in self.atualizar_texto + self.atualizar_meta]
        return self.remover + atualizados


def planejar_documentos(gravados: dict[str, dict], lidos: dict[str, dict]) -> PlanoDocumentos:
    """Compara documentos pelo hash do arquivo inteiro (barato: nem precisa fazer o chunking)."""
    plano = PlanoDocumentos()
    for doc_id, doc in sorted(lidos.items()):
        antigo = gravados.get(doc_id)
        if antigo is None:
            plano.novos.append(doc_id)
        elif antigo["hash_arquivo"] != doc["hash_arquivo"]:
            plano.alterados.append(doc_id)
        elif sorted(antigo.get("acl") or []) != sorted(doc.get("acl") or []):
            plano.so_acl.append(doc_id)
        else:
            plano.iguais.append(doc_id)
    plano.removidos = sorted(set(gravados) - set(lidos))
    return plano


def planejar_chunks(existentes: dict[str, dict], novos: list[dict]) -> PlanoChunks:
    """Compara os chunks de UM documento pelo chunk_id e pelo hash do texto.

    `existentes`: chunk_id -> linha gravada em kb_chunks (com o vetor).
    `novos`: chunks recém-gerados pelo chunking, com acl e metadados, ainda sem vetor.
    """
    plano = PlanoChunks()
    ids_novos = set()
    for chunk in novos:
        ids_novos.add(chunk["chunk_id"])
        antigo = existentes.get(chunk["chunk_id"])
        if antigo is None:
            plano.inserir.append({**chunk, "embedding": None, "modelo_embedding": None, "status_embedding": "pendente"})
        elif antigo["hash_conteudo"] != chunk["hash_conteudo"]:
            plano.atualizar_texto.append({**chunk, "embedding": None, "modelo_embedding": None, "status_embedding": "pendente"})
        elif (
            sorted(antigo.get("acl") or []) != sorted(chunk.get("acl") or [])
            or antigo.get("secao") != chunk.get("secao")
            or antigo.get("ordem") != chunk.get("ordem")
        ):
            # texto igual: o vetor continua válido, só os metadados mudam
            plano.atualizar_meta.append({**chunk, **{campo: antigo.get(campo) for campo in CAMPOS_DO_VETOR}})
        else:
            plano.mantidos += 1
    plano.remover = sorted(set(existentes) - ids_novos)
    return plano
