"""Geração de embeddings com um modelo local no Ollama.

Usa só a biblioteca padrão (urllib) para falar com a API do Ollama:
    POST /api/embed  {"model": "bge-m3", "input": ["texto 1", "texto 2", ...]}
    -> {"embeddings": [[0.01, -0.03, ...], [...]]}
"""

import json
import time
import urllib.error
import urllib.request

from . import config


class ErroOllama(RuntimeError):
    pass


def texto_para_embedding(titulo: str, secao: str, texto: str) -> str:
    """O que vai para o modelo: o chunk com um cabeçalho de contexto.

    "Art. 49. O consumidor pode desistir..." sozinho não diz de qual lei é. Com o
    cabeçalho, o vetor também carrega "Código de Defesa do Consumidor, Da Proteção
    Contratual". O texto guardado na tabela continua sendo só o chunk.
    """
    return f"{titulo}\n{secao}\n\n{texto}"


def gerar_embeddings(textos: list[str], tentativas: int = 3) -> list[list[float]]:
    """Envia um lote de textos ao Ollama, com novas tentativas se ele falhar."""
    corpo = json.dumps({"model": config.MODELO_EMBEDDING, "input": textos}).encode("utf-8")
    pedido = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/embed",
        data=corpo,
        headers={"Content-Type": "application/json"},
    )
    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(pedido, timeout=300) as resposta:
                vetores = json.loads(resposta.read())["embeddings"]
            if len(vetores) != len(textos):
                raise ErroOllama(f"pedi {len(textos)} vetores e recebi {len(vetores)}")
            return vetores
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")
            if "not found" in detalhe:
                raise ErroOllama(
                    f"modelo '{config.MODELO_EMBEDDING}' não encontrado. Rode: ollama pull {config.MODELO_EMBEDDING}"
                ) from erro
            ultimo_erro: Exception = erro
        except urllib.error.URLError as erro:
            ultimo_erro = erro
        if tentativa < tentativas:
            time.sleep(2 ** tentativa)   # espera 2 s, depois 4 s
    raise ErroOllama(
        f"não consegui falar com o Ollama em {config.OLLAMA_URL} ({ultimo_erro}). Ele está aberto?"
    )
