from pathlib import Path

from busca_lexical import BuscaLexical, indexar
from recuperacao import carregar_trechos, validar_catalogo


ROOT = Path(__file__).resolve().parents[1]


def test_catalogo_cobre_o_corpus_atual():
    trechos = carregar_trechos(ROOT)
    assert len(trechos) == 101
    assert all(item["expressions"] for item in trechos)
    assert all(1 <= len(item["dense_descriptions"]) <= 3 for item in trechos)


def test_catalogo_atual_e_valido():
    texto = (ROOT / "corpus" / "retrieval-metadata.v1.json").read_text(encoding="utf-8")
    assert validar_catalogo(ROOT, texto) == []


def test_expressao_participa_da_busca_lexical():
    # O índice é um artefato reconstruível do próprio módulo.
    root = ROOT
    indexar(root)
    resultados = BuscaLexical(root).search("afastamento do lar", limit=10)
    assert any(item["id"] == "lei-11340-2006:art22-p1" for item in resultados)
