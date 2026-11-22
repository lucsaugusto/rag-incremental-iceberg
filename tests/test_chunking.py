from rag.chunking import (
    dividir_artigo_longo,
    dividir_em_artigos,
    estimar_tokens,
    gerar_chunks,
    limpar_texto,
)

LEI_EXEMPLO = """21/11/2026, 22:30 L8078compilado
LEI Nº 8.078, DE 11 DE SETEMBRO DE 1990.
Dispõe sobre a proteção do consumidor e dá outras providências.
TÍTULO I
DOS DIREITOS DO CONSUMIDOR
CAPÍTULO I
DISPOSIÇÕES GERAIS
Art. 1° O presente código estabelece normas de proteção e defesa do consumidor,
nos termos do art. 5° da Constituição Federal.
Art. 2° Consumidor é toda pessoa física ou jurídica que adquire ou utiliza pro-
duto ou serviço como destinatário final.
CAPÍTULO VI
DA PROTEÇÃO CONTRATUAL
Art. 49. O consumidor pode desistir do contrato, no prazo de 7 dias a contar de sua assinatura.
Parágrafo único. Os valores eventualmente pagos serão devolvidos.
Art. 55-A. Artigo inserido por lei posterior.
https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm 3/45
"""


def test_limpeza_remove_ruido_e_hifenizacao():
    texto = limpar_texto(LEI_EXEMPLO)
    assert "planalto.gov.br" not in texto
    assert "22:30" not in texto
    assert "produto ou serviço" in texto


def test_artigos_separados_com_capitulo():
    artigos = dividir_em_artigos(limpar_texto(LEI_EXEMPLO))
    slugs = [a.slug for a in artigos]
    assert slugs == ["preambulo", "art-1", "art-2", "art-49", "art-55-a"]
    art49 = next(a for a in artigos if a.slug == "art-49")
    assert art49.capitulo == "CAPÍTULO VI – DA PROTEÇÃO CONTRATUAL"
    art1 = next(a for a in artigos if a.slug == "art-1")
    assert art1.capitulo == "CAPÍTULO I – DISPOSIÇÕES GERAIS"
    assert "Parágrafo único" in art49.texto
    # a referência "art. 5°" no meio do texto não vira artigo novo
    assert "Constituição Federal" in next(a for a in artigos if a.slug == "art-1").texto


def test_artigo_recuado_com_espaco_nao_separavel():
    # Formato real do site do Planalto: recuo feito com
    texto = "Lei\n        Art. 49. O consumidor pode desistir.\n   Art. 50. Outro."
    artigos = dividir_em_artigos(limpar_texto(texto))
    assert [a.slug for a in artigos] == ["preambulo", "art-49", "art-50"]


def test_titulo_de_capitulo_nao_fica_no_texto_do_artigo_anterior():
    artigos = dividir_em_artigos(limpar_texto(LEI_EXEMPLO))
    art2 = next(a for a in artigos if a.slug == "art-2")
    assert "CAPÍTULO VI" not in art2.texto
    assert "PROTEÇÃO CONTRATUAL" not in art2.texto


def test_chunk_id_estavel_quando_artigo_e_inserido_antes():
    original = gerar_chunks("cdc", LEI_EXEMPLO, 400, 50)
    com_insercao = gerar_chunks(
        "cdc", LEI_EXEMPLO.replace("Art. 49.", "Art. 48-A. Artigo novo.\nArt. 49."), 400, 50
    )
    hash_49 = {c["chunk_id"]: c["hash_conteudo"] for c in original}["cdc#art-49"]
    hash_49_depois = {c["chunk_id"]: c["hash_conteudo"] for c in com_insercao}["cdc#art-49"]
    assert hash_49 == hash_49_depois           # mesmo id, mesmo conteúdo: nada a reprocessar
    assert "cdc#art-48-a" in {c["chunk_id"] for c in com_insercao}


def test_artigo_longo_dividido_com_sobreposicao():
    incisos = "\n".join(f"{i} - inciso número {i} com um texto razoavelmente comprido." for i in range(1, 60))
    partes = dividir_artigo_longo("Art. 6º São direitos básicos:\n" + incisos, 120, 30)
    assert len(partes) > 1
    assert all(estimar_tokens(p) <= 120 for p in partes)
    # sobreposição: a parte 2 começa repetindo as últimas linhas da parte 1
    linhas_1, linhas_2 = partes[0].split("\n"), partes[1].split("\n")
    repetidas = [linha for linha in linhas_2 if linha in linhas_1]
    assert repetidas and linhas_1[-len(repetidas):] == repetidas
    assert estimar_tokens("\n".join(repetidas)) <= 30


def test_chunks_longos_recebem_sufixo_de_parte():
    incisos = "\n".join(f"{i} - inciso {i} " + "texto " * 15 for i in range(1, 40))
    chunks = gerar_chunks("cdc", "Art. 6º São direitos básicos:\n" + incisos, 120, 30)
    assert chunks[0]["chunk_id"] == "cdc#art-6-p1"
    assert "(parte 1/" in chunks[0]["secao"]


def test_artigo_repetido_mantem_a_ultima_redacao():
    texto = """CAPÍTULO III
DO AGENDAMENTO
Art. 9º Redação original.
Art. 9º Redação de 2021. (Redação dada pela Resolução BCB nº 118.)
Art. 9º Redação de 2022. (Redação dada pela Resolução BCB nº 181.)
Art. 10. Outro artigo."""
    estatisticas = {}
    artigos = dividir_em_artigos(limpar_texto(texto), estatisticas)
    assert [a.slug for a in artigos] == ["art-9", "art-10"]
    assert "Redação de 2022" in artigos[0].texto
    assert "Redação original" not in artigos[0].texto
    assert estatisticas["versoes_antigas_descartadas"] == 2


def test_secao_mantem_o_capitulo_e_capitulo_novo_zera_a_secao():
    texto = """CAPÍTULO V
DAS PRÁTICAS COMERCIAIS
Seção II
Da Oferta
Art. 30. Toda informação suficientemente precisa vincula o fornecedor.
CAPÍTULO VI
DA PROTEÇÃO CONTRATUAL
Art. 46. Os contratos não obrigarão os consumidores."""
    artigos = {a.slug: a for a in dividir_em_artigos(limpar_texto(texto))}
    assert artigos["art-30"].capitulo == "CAPÍTULO V – DAS PRÁTICAS COMERCIAIS › Seção II – Da Oferta"
    assert artigos["art-46"].capitulo == "CAPÍTULO VI – DA PROTEÇÃO CONTRATUAL"


def test_numero_de_artigo_quebrado_pelo_site():
    artigos = dividir_em_artigos(limpar_texto("Art. 56. Algo.\nArt. 5 7. (VETADO)."))
    assert [a.slug for a in artigos] == ["art-56", "art-57"]


def test_nota_de_revisao_nao_vira_nome_do_capitulo():
    texto = "CAPÍTULO IX\n(Redação dada pela Lei nº 15.352, de 2026)\nDA AGÊNCIA NACIONAL\nArt. 55-A. Texto."
    artigos = dividir_em_artigos(limpar_texto(texto))
    assert artigos[0].capitulo == "CAPÍTULO IX – DA AGÊNCIA NACIONAL"
