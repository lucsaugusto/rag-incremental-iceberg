"""Limpeza do texto extraído e divisão em chunks, um por artigo de lei.

Por que um chunk por artigo?
- Em leis, o artigo é a unidade natural de sentido: "o que diz o art. 49 do CDC?".
- O chunk_id fica ESTÁVEL: "cdc#art-49" continua sendo "cdc#art-49" mesmo que um artigo
  novo seja inserido antes dele. Se o id fosse a posição (chunk 1, 2, 3...), inserir um
  artigo mudaria o id de todos os seguintes, e a etapa incremental acharia que tudo mudou.
"""

import hashlib
import math
import re
from dataclasses import dataclass

# "Art. 1º", "Art. 10.", "Art. 5º-A", "Art. 2o" no início da linha.
# Referências no meio do texto ("nos termos do art. 49") começam com minúscula e não casam.
RE_ARTIGO = re.compile(r"^[ \t]*Art\.\s*(\d+)(?:[º°o])?(?:\s*-\s*([A-Z])\b)?", re.MULTILINE)

# Títulos de agrupamento: "CAPÍTULO VI", "TÍTULO II", "Seção III".
RE_AGRUPAMENTO = re.compile(r"^\s*(TÍTULO|CAPÍTULO|SEÇÃO|Seção|Subseção)\s+[IVXLCDM]+\b")

# Ruído típico de PDF gerado pelo navegador (Imprimir -> Salvar como PDF).
RE_RUIDO = [
    re.compile(r"(?m)^.*https?://\S+.*$\n?"),                       # rodapé com a URL
    re.compile(r"(?m)^\s*\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}.*$\n?"),  # cabeçalho com data/hora
    re.compile(r"(?m)^\s*\d+\s*/\s*\d+\s*$\n?"),                    # numeração "3/45"
    re.compile(r"(?m)^\s*\d+\s*$\n?"),                              # número de página sozinho
]


@dataclass
class Artigo:
    rotulo: str            # "Art. 49"
    slug: str              # "art-49"
    capitulo: str | None   # "CAPÍTULO VI – DA PROTEÇÃO CONTRATUAL › Seção II – Da Oferta"
    texto: str

# Nível de cada agrupamento: um CAPÍTULO novo zera a seção anterior, e assim por diante.
NIVEIS = {"TÍTULO": 0, "CAPÍTULO": 1, "SEÇÃO": 2, "Seção": 2, "Subseção": 3}


def estimar_tokens(texto: str) -> int:
    """Estimativa simples: ~4 caracteres por token. Suficiente para dimensionar chunks."""
    return max(1, math.ceil(len(texto) / 4))


def hash_texto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def limpar_texto(texto: str) -> str:
    """Remove ruído de PDF e normaliza espaços, preservando as quebras de linha."""
    t = texto.replace("\r\n", "\n").replace("\r", "\n")
    # Espaço "não separável" ( ): o site do Planalto o usa para recuar os artigos.
    # Sem esta troca, "    Art. 49" não seria reconhecido como início de artigo.
    t = t.replace(" ", " ").replace("\t", " ")
    for padrao in RE_RUIDO:
        t = padrao.sub("", t)
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)    # palavra hifenizada na quebra de linha
    t = re.sub(r"(Art\.\s*\d+) (\d+\.)", r"\1\2", t)  # número quebrado pelo site: "Art. 5 7." -> "Art. 57."
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def dividir_em_artigos(texto: str, estatisticas: dict | None = None) -> list[Artigo]:
    """Separa o texto em artigos e registra em qual capítulo/seção cada um está.

    Páginas "compiladas" de normas trazem as redações antigas de um artigo antes da atual
    (o Art. 9 do Regulamento do Pix aparece três vezes). Regra adotada: quando um artigo
    se repete, fica a ÚLTIMA redação, na posição em que o artigo apareceu pela primeira vez.
    """
    por_slug: dict[str, Artigo] = {}      # dict preserva a ordem da primeira ocorrência
    niveis: dict[int, str] = {}
    versoes_descartadas = 0

    def contexto() -> str | None:
        partes = [niveis[n] for n in sorted(niveis) if n >= 1]   # TÍTULO fica de fora (muito genérico)
        return " › ".join(partes) or None

    def processar_bloco(bloco: str, rotulo: str, slug: str) -> None:
        nonlocal versoes_descartadas
        contexto_do_bloco = contexto()     # títulos achados aqui valem para o PRÓXIMO artigo
        linhas: list[str] = []
        nivel_esperando_nome: int | None = None
        for linha in bloco.split("\n"):
            limpa = linha.strip()
            m = RE_AGRUPAMENTO.match(limpa)
            if m:
                nivel = NIVEIS[m.group(1)]
                niveis[nivel] = limpa
                for mais_fundo in [n for n in niveis if n > nivel]:
                    del niveis[mais_fundo]
                nivel_esperando_nome = nivel
                continue
            if nivel_esperando_nome is not None and limpa.startswith("("):
                continue   # nota de revisão do título, ex.: "(Redação dada pela Lei nº ...)"
            if nivel_esperando_nome is not None and limpa:
                nivel = nivel_esperando_nome
                nivel_esperando_nome = None
                # o nome do agrupamento vem na linha seguinte ("DA PROTEÇÃO CONTRATUAL", "Da Oferta")
                if not RE_ARTIGO.match(limpa) and len(limpa) < 150 and not limpa.endswith((".", ":", ";")):
                    niveis[nivel] = f"{niveis[nivel]} – {limpa}"
                    continue
            linhas.append(linha)
        corpo = "\n".join(linhas).strip()
        if not corpo:
            return
        if slug in por_slug:
            versoes_descartadas += 1
            contexto_do_bloco = por_slug[slug].capitulo or contexto_do_bloco
        por_slug[slug] = Artigo(rotulo, slug, contexto_do_bloco, corpo)

    marcas = list(RE_ARTIGO.finditer(texto))
    inicio_primeiro = marcas[0].start() if marcas else len(texto)
    processar_bloco(texto[:inicio_primeiro], "Preâmbulo", "preambulo")

    for i, m in enumerate(marcas):
        fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        numero, letra = m.group(1), m.group(2)
        rotulo = f"Art. {numero}" + (f"-{letra}" if letra else "")
        slug = f"art-{numero}" + (f"-{letra.lower()}" if letra else "")
        processar_bloco(texto[m.start():fim], rotulo, slug)

    if estatisticas is not None:
        estatisticas["versoes_antigas_descartadas"] = versoes_descartadas
    return list(por_slug.values())


def _quebrar_linha_longa(linha: str, max_tokens: int) -> list[str]:
    """Último recurso: uma única linha maior que o limite é cortada por palavras."""
    partes, atual = [], ""
    for palavra in linha.split(" "):
        candidato = f"{atual} {palavra}".strip()
        if atual and estimar_tokens(candidato) > max_tokens:
            partes.append(atual)
            atual = palavra
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes


def dividir_artigo_longo(texto: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Divide um artigo grande por linhas (parágrafos, incisos), repetindo o final da parte
    anterior no começo da próxima (sobreposição), para não cortar uma ideia no meio."""
    linhas: list[str] = []
    for linha in texto.split("\n"):
        if estimar_tokens(linha) > max_tokens:
            linhas.extend(_quebrar_linha_longa(linha, max_tokens))
        elif linha.strip():
            linhas.append(linha)

    partes: list[str] = []
    atual: list[str] = []
    for linha in linhas:
        if atual and estimar_tokens("\n".join(atual + [linha])) > max_tokens:
            partes.append("\n".join(atual))
            # sobreposição: últimas linhas da parte anterior, até o limite de overlap
            sobra: list[str] = []
            for anterior in reversed(atual):
                if estimar_tokens("\n".join([anterior] + sobra)) > overlap_tokens:
                    break
                sobra.insert(0, anterior)
            atual = sobra
        atual.append(linha)
    if atual:
        partes.append("\n".join(atual))
    return partes


def gerar_chunks(
    doc_id: str, texto_bruto: str, max_tokens: int, overlap_tokens: int, estatisticas: dict | None = None
) -> list[dict]:
    """Do texto extraído do documento até a lista de chunks."""
    artigos = dividir_em_artigos(limpar_texto(texto_bruto), estatisticas)
    chunks: list[dict] = []
    for artigo in artigos:
        secao_base = f"{artigo.capitulo} · {artigo.rotulo}" if artigo.capitulo else artigo.rotulo
        if estimar_tokens(artigo.texto) <= max_tokens:
            pedacos = [(artigo.slug, secao_base, artigo.texto)]
        else:
            partes = dividir_artigo_longo(artigo.texto, max_tokens, overlap_tokens)
            pedacos = [
                (f"{artigo.slug}-p{i}", f"{secao_base} (parte {i}/{len(partes)})", parte)
                for i, parte in enumerate(partes, start=1)
            ]
        for id_parte, secao, texto in pedacos:
            chunks.append({
                "chunk_id": f"{doc_id}#{id_parte}",
                "doc_id": doc_id,
                "secao": secao,
                "ordem": len(chunks) + 1,
                "texto": texto,
                "tokens": estimar_tokens(texto),
                "hash_conteudo": hash_texto(texto),
            })
    return chunks
