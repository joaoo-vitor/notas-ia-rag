import unittest
from collections import defaultdict

from avaliar import metricas, rotulos_por_trecho
from busca_lexical import PESOS_BM25
from ingestao import extrair_trechos_textuais


class RagTests(unittest.TestCase):
    def test_extracts_declared_textual_excerpt_from_official_page(self):
        html = """
        <html><body><nav>Texto que não interessa</nav>
        <p>Introdução.</p>
        <p>A proteção alcança mulheres trans, independentemente do gênero do agressor.</p>
        <p>Rodapé.</p></body></html>
        """
        [trecho] = extrair_trechos_textuais(html, [{
            "id": "entendimento",
            "secao": "Decisão",
            "contexto": "Jurisprudência",
            "contem": "mulheres trans, independentemente do genero",
        }])
        self.assertEqual(trecho["id"], "entendimento")
        self.assertEqual(
            trecho["texto"],
            "A proteção alcança mulheres trans, independentemente do gênero do agressor.",
        )

    def test_missing_declared_excerpt_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "fonte pode ter mudado"):
            extrair_trechos_textuais("<p>Outro texto</p>", [{
                "id": "ausente", "secao": "Decisão", "contem": "frase esperada"
            }])

    def test_bm25_weights_follow_all_fts_columns(self):
        self.assertEqual(PESOS_BM25, (0.0, 1.0, 0.3, 0.2))

    def test_unsplit_article_maps_to_its_only_chunk(self):
        mapa = defaultdict(list, {"lei:art7": ["lei:art7"]})
        relevantes, parciais, pendentes = rotulos_por_trecho(
            {"relevantes": ["lei:art7"]}, mapa
        )
        self.assertEqual(relevantes, {"lei:art7"})
        self.assertEqual(parciais, set())
        self.assertEqual(pendentes, [])

    def test_split_article_requires_explicit_part(self):
        mapa = defaultdict(list, {"lei:art22": ["lei:art22-p1", "lei:art22-p2"]})
        _, _, pendentes = rotulos_por_trecho(
            {"relevantes": ["lei:art22"]}, mapa
        )
        self.assertEqual(pendentes, ["lei:art22"])

        relevantes, _, pendentes = rotulos_por_trecho({
            "relevantes": ["lei:art22"],
            "trechos_relevantes": ["lei:art22-p2"],
        }, mapa)
        self.assertEqual(relevantes, {"lei:art22-p2"})
        self.assertEqual(pendentes, [])

    def test_wrong_part_is_not_a_chunk_hit(self):
        resultado = metricas(
            [("lei:art22-p1", 0.9), ("lei:art22-p2", 0.8)],
            {"lei:art22-p2"},
            set(),
        )
        self.assertEqual(resultado["acerto@1"], 0.0)
        self.assertEqual(resultado["acerto@3"], 1.0)
        self.assertEqual(resultado["MRR"], 0.5)


if __name__ == "__main__":
    unittest.main()
