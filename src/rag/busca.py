"""Busca por similaridade de cosseno, com o filtro de ACL aplicado ANTES do ranking.

Com 574 chunks, comparar a pergunta com todos (força bruta) leva milissegundos.
Em produção, com milhões de chunks, essa etapa vira um índice vetorial (OpenSearch,
pgvector...), mas a ideia é a mesma: filtrar pela permissão e ordenar pela similaridade.
"""

import math


def cosseno(a: list[float], b: list[float]) -> float:
    """1 = mesma direção (mesmo sentido), 0 = sem relação."""
    produto = sum(x * y for x, y in zip(a, b))
    norma_a = math.sqrt(sum(x * x for x in a))
    norma_b = math.sqrt(sum(y * y for y in b))
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return produto / (norma_a * norma_b)


def pode_ver(acl: list[str] | None, grupos_do_usuario: list[str]) -> bool:
    """Acl vazia ou ausente = ninguém vê (padrão seguro)."""
    return bool(acl) and bool(set(acl) & set(grupos_do_usuario))


def buscar(
    vetor_pergunta: list[float],
    chunks: list[dict],
    grupos_do_usuario: list[str],
    k: int,
) -> tuple[list[dict], int]:
    """Devolve os k chunks mais parecidos que o usuário pode ver, e quantos foram bloqueados."""
    permitidos = [c for c in chunks if pode_ver(c.get("acl"), grupos_do_usuario)]   # 1. filtra
    bloqueados = len(chunks) - len(permitidos)
    com_score = [
        {**c, "score": cosseno(vetor_pergunta, c["embedding"])}
        for c in permitidos
        if c.get("embedding")
    ]
    com_score.sort(key=lambda c: c["score"], reverse=True)                            # 2. ordena
    return com_score[:k], bloqueados
