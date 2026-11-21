"""Testes da geração que não chamam o Gemini: montagem do pedido, citações e oráculo."""

import unittest

import avaliar_geracao
import geracao
from avaliar import mapa_trechos


def saida(resposta, citacoes=(), sem_base=False):
    return {"json": {"resposta": resposta, "citacoes": list(citacoes), "sem_base": sem_base},
            "modelo": "m", "fim": "STOP", "uso": {}}


PERGUNTA = {"id": "q1", "tipo": "coloquial", "status": "revisado", "pergunta": "x"}


class TestCitacoes(unittest.TestCase):
    def test_citacoes_vem_das_marcas_no_texto(self):
        ids = ["a:art1", "b:art2", "c:art3"]
        item = geracao.registro(PERGUNTA, "lexical", ids, {}, "m",
                                saida("Isso é crime [3]. Também [1] e de novo [3].", citacoes=[1]))
        self.assertEqual(item["citacoes_texto"], [3, 1])
        self.assertEqual(item["citacoes"], ["c:art3", "a:art1"])
        self.assertEqual(item["citacoes_declaradas"], [1])
        self.assertEqual(item["citacoes_invalidas"], [])

    def test_varios_trechos_nos_mesmos_colchetes(self):
        item = geracao.registro(PERGUNTA, "lexical", ["a:art1", "b:art2", "c:art3", "d:art4"], {}, "m",
                                saida("Isso é violência [1, 4]. E também [2;3]."))
        self.assertEqual(item["citacoes"], ["a:art1", "d:art4", "b:art2", "c:art3"])

    def test_citacao_fora_da_lista_e_invalida(self):
        item = geracao.registro(PERGUNTA, "lexical", ["a:art1"], {}, "m", saida("Veja [2] e [1]."))
        self.assertEqual(item["citacoes_invalidas"], [2])
        self.assertEqual(item["citacoes"], ["a:art1"])

    def test_sem_busca_guarda_artigos_citados(self):
        dados = saida("Pela Lei Maria da Penha...")
        dados["json"] = {"resposta": "Pela lei...", "artigos_citados": ["Lei 11.340/2006, art. 7"],
                         "sem_base": False}
        item = geracao.registro(PERGUNTA, "sem-busca", None, {}, "m", dados)
        self.assertEqual(item["artigos_citados"], ["Lei 11.340/2006, art. 7"])
        self.assertNotIn("citacoes", item)


class TestPedido(unittest.TestCase):
    def test_trechos_numerados_e_esquema(self):
        trechos = {"a:art1": {"documento": "Lei A", "secao": "Art. 1", "texto": "texto um"}}
        corpo = geracao.montar_pedido("pergunta?", ["a:art1"], trechos)
        texto = corpo["contents"][0]["parts"][0]["text"]
        self.assertIn("[1] Lei A, Art. 1\ntexto um", texto)
        self.assertIn("citacoes", corpo["generationConfig"]["responseSchema"]["properties"])

    def test_lista_vazia_avisa_que_nao_ha_trechos(self):
        corpo = geracao.montar_pedido("pergunta?", [], {})
        self.assertIn("(nenhum trecho encontrado)", corpo["contents"][0]["parts"][0]["text"])

    def test_sem_busca_nao_tem_trechos(self):
        corpo = geracao.montar_pedido("pergunta?", None, {})
        self.assertNotIn("Trechos de lei", corpo["contents"][0]["parts"][0]["text"])
        self.assertIn("artigos_citados", corpo["generationConfig"]["responseSchema"]["properties"])

    def test_hash_muda_com_o_pedido(self):
        a = geracao.montar_pedido("p1", [], {})
        b = geracao.montar_pedido("p2", [], {})
        self.assertNotEqual(geracao.hash_do_pedido(a, "m"), geracao.hash_do_pedido(b, "m"))
        self.assertNotEqual(geracao.hash_do_pedido(a, "m1"), geracao.hash_do_pedido(a, "m2"))


class TestOraculo(unittest.TestCase):
    def setUp(self):
        self.por_artigo = mapa_trechos(geracao.TRECHOS)

    def test_usa_partes_exatas_quando_marcadas(self):
        p = {"relevantes": ["lei-11340-2006:art7"], "trechos_relevantes": ["lei-11340-2006:art7-p4"]}
        self.assertEqual(geracao.trechos_do_oraculo(p, self.por_artigo), ["lei-11340-2006:art7-p4"])

    def test_artigo_dividido_sem_parte_marcada_entra_inteiro(self):
        p = {"relevantes": ["lei-11340-2006:art7"]}
        self.assertEqual(geracao.trechos_do_oraculo(p, self.por_artigo),
                         self.por_artigo["lei-11340-2006:art7"])

    def test_sem_resposta_fica_vazio(self):
        self.assertEqual(geracao.trechos_do_oraculo({"relevantes": []}, self.por_artigo), [])


class TestMetricas(unittest.TestCase):
    P = {"relevantes": ["lei-11340-2006:art7"], "parciais": ["lei-11340-2006:art5"]}

    def resposta(self, citacoes, sem_base=False, invalidas=()):
        return {"sem_base": sem_base, "citacoes": citacoes, "citacoes_invalidas": list(invalidas)}

    def test_citou_relevante_e_precisao(self):
        m, desfecho = avaliar_geracao.medir(
            self.P, self.resposta(["lei-11340-2006:art7-p4", "lei-11340-2006:art5", "codigo-penal:art163"]),
            ["lei-11340-2006:art7-p4"])
        self.assertEqual(desfecho, "ok")
        self.assertEqual(m["citou_relevante"], 1.0)
        self.assertAlmostEqual(m["precisao_citacao"], 2 / 3)
        self.assertAlmostEqual(m["precisao_estrita"], 1 / 3)

    def test_erro_de_geracao_e_de_busca(self):
        _, d1 = avaliar_geracao.medir(self.P, self.resposta(["codigo-penal:art163"]),
                                      ["lei-11340-2006:art7-p4", "codigo-penal:art163"])
        _, d2 = avaliar_geracao.medir(self.P, self.resposta(["codigo-penal:art163"]),
                                      ["codigo-penal:art163"])
        self.assertEqual((d1, d2), ("erro_geracao", "erro_busca"))

    def test_recusas(self):
        m, d = avaliar_geracao.medir({"relevantes": []}, self.resposta([], sem_base=True), [])
        self.assertEqual((m["recusa_correta"], d), (1.0, "recusa_correta"))
        m, d = avaliar_geracao.medir(self.P, self.resposta([], sem_base=True), [])
        self.assertEqual((m["recusa_indevida"], d), (1.0, "recusa_indevida"))
        self.assertIsNone(m["citou_relevante"])

    def test_f1_da_recusa(self):
        linhas = [{"sem_resposta": True, "recusou": True}, {"sem_resposta": True, "recusou": False},
                  {"sem_resposta": False, "recusou": True}, {"sem_resposta": False, "recusou": False}]
        self.assertEqual(avaliar_geracao.f1_recusa(linhas), {"precisao": 0.5, "cobertura": 0.5, "f1": 0.5})

    def test_artigos_por_extenso(self):
        self.assertEqual(
            avaliar_geracao.artigos_por_extenso(
                ["Lei 11.340/2006, art. 7", "Código Penal, arts. 129 e 147-A", "Lei 8.069/1990, art. 5"]),
            ["lei-11340-2006:art7", "codigo-penal:art129", "codigo-penal:art147-A"])


if __name__ == "__main__":
    unittest.main()
