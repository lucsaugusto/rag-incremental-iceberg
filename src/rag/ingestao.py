"""Leitura dos arquivos (.pdf e .txt) da pasta data/fontes: texto, hash do arquivo e permissões (ACL)."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pypdf import PdfReader

from . import config


def hash_arquivo(caminho: Path) -> str:
    """Impressão digital do arquivo inteiro: muda se qualquer byte mudar."""
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


EXTENSOES_ACEITAS = (".pdf", ".txt")


def extrair_texto(caminho: Path) -> tuple[str, int | None]:
    """Devolve o texto e o número de páginas (None para .txt, que não tem páginas)."""
    if caminho.suffix.lower() == ".txt":
        return caminho.read_text(encoding="utf-8"), None
    leitor = PdfReader(str(caminho))
    paginas = [pagina.extract_text() or "" for pagina in leitor.pages]
    return "\n".join(paginas), len(paginas)


def carregar_acl(arquivo: Path) -> dict:
    if not arquivo.exists():
        return {}
    conteudo = yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}
    return conteudo.get("documentos") or {}


def ler_documentos() -> tuple[list[dict], list[str]]:
    """Lê os arquivos .pdf e .txt da pasta de fontes. Devolve as linhas da tabela e os avisos."""
    configuracao_acl = carregar_acl(config.ARQUIVO_ACL)
    agora = datetime.now(timezone.utc)
    linhas, avisos = [], []

    arquivos = sorted(p for p in config.PASTA_FONTES.glob("*") if p.suffix.lower() in EXTENSOES_ACEITAS)
    for arquivo in arquivos:
        doc_id = arquivo.stem.lower()
        cfg = configuracao_acl.get(doc_id, {})
        acl = list(cfg.get("acl") or [])
        if not acl:
            avisos.append(f"{doc_id}: sem ACL em data/acl.yaml -> ninguém verá este documento na busca")

        texto, paginas = extrair_texto(arquivo)
        if len(texto.strip()) < 200:
            avisos.append(f"{doc_id}: quase nenhum texto extraído (PDF escaneado?) -> precisaria de OCR")

        linhas.append({
            "doc_id": doc_id,
            "arquivo": arquivo.relative_to(config.RAIZ).as_posix(),
            "titulo": cfg.get("titulo", doc_id),
            "hash_arquivo": hash_arquivo(arquivo),
            "tamanho_bytes": arquivo.stat().st_size,
            "paginas": paginas,
            "texto": texto,
            "acl": acl,
            "lido_em": agora,
        })
    return linhas, avisos
