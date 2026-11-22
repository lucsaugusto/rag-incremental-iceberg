import pytest

from rag.busca import buscar, cosseno, pode_ver
from rag.embeddings import texto_para_embedding


def test_cosseno():
    assert cosseno([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosseno([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosseno([1, 1], [2, 2]) == pytest.approx(1.0)   # só a direção importa, não o tamanho
    assert cosseno([0, 0], [1, 0]) == 0.0


def test_acl_vazia_ninguem_ve():
    assert pode_ver(["publico"], ["publico"])
    assert not pode_ver(["juridico"], ["publico"])
    assert not pode_ver([], ["publico"])
    assert not pode_ver(None, ["publico"])


CHUNKS = [
    {"chunk_id": "cdc#art-49", "acl": ["publico"], "embedding": [0.9, 0.1]},
    {"chunk_id": "pix#art-9", "acl": ["atendimento", "juridico"], "embedding": [1.0, 0.0]},
    {"chunk_id": "lgpd#art-7", "acl": ["publico", "juridico"], "embedding": [0.0, 1.0]},
    {"chunk_id": "sem-vetor", "acl": ["publico"], "embedding": None},
]


def test_filtro_de_acl_vem_antes_do_ranking():
    # o chunk mais parecido (pix#art-9) não é visível para o grupo "publico"
    resultado, bloqueados = buscar([1.0, 0.0], CHUNKS, ["publico"], k=2)
    assert [c["chunk_id"] for c in resultado] == ["cdc#art-49", "lgpd#art-7"]
    assert bloqueados == 1


def test_mesmo_vetor_resultados_diferentes_por_grupo():
    resultado, _ = buscar([1.0, 0.0], CHUNKS, ["atendimento"], k=1)
    assert resultado[0]["chunk_id"] == "pix#art-9"


def test_texto_para_embedding_leva_contexto():
    entrada = texto_para_embedding("Código de Defesa do Consumidor", "CAPÍTULO VI · Art. 49", "Art. 49. O consumidor...")
    assert entrada.startswith("Código de Defesa do Consumidor\nCAPÍTULO VI · Art. 49")
    assert entrada.endswith("Art. 49. O consumidor...")
