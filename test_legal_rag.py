"""Testes do servidor MCP, chamado do mesmo jeito que o backend (ClientGroup com prefixo "legal").

Usam a BM25 para não carregar modelos de embeddings.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastmcp import Client, ClientGroup

import busca_densa
from legal_rag.busca import Buscador, normalizar_estrategia
from legal_rag.servidor import criar_servidor

CAMPOS = {"chunk_id", "document", "section", "url", "verified_at", "text"}


def chamar(buscador, argumentos):
    async def rodar():
        grupo = ClientGroup({"legal": Client(criar_servidor(buscador))})
        async with grupo:
            nomes = [t.name for t in await grupo.list_tools()]
            resultado = await grupo.call_tool("legal_search_legal_sources", argumentos,
                                              raise_on_error=True)
            return nomes, resultado.structured_content
    return asyncio.run(rodar())


class ServidorMcpTests(unittest.TestCase):
    def test_tool_name_and_response_follow_backend_contract(self):
        nomes, dados = chamar(Buscador("bm25"), {"query": "ele quebrou meu celular", "limit": 3})
        self.assertEqual(nomes, ["legal_search_legal_sources"])
        self.assertEqual(dados["effective_strategy"], "lexical")
        self.assertIsNone(dados["fallback_reason"])
        self.assertTrue(dados["corpus_version"])
        self.assertEqual(len(dados["results"]), 3)
        for item in dados["results"]:
            self.assertEqual(set(item), CAMPOS)
            self.assertTrue(item["url"].startswith("https://"))

    def test_results_carry_only_official_text(self):
        _, dados = chamar(Buscador("bm25"), {"query": "medida protetiva"})
        for item in dados["results"]:
            self.assertNotIn("Termos relacionados", item["text"])
            self.assertNotIn("enriquecimento", item)

    def test_dense_strategy_without_index_falls_back_to_bm25(self):
        with tempfile.TemporaryDirectory() as vazio, \
                mock.patch.object(busca_densa, "INDICES", Path(vazio)):
            buscador = Buscador("hibrida-bge-m3")
        self.assertEqual(buscador.efetiva, "lexical")
        self.assertIn("hibrida-bge-m3", buscador.fallback_reason)
        self.assertEqual(buscador.pronto()["status"], "degraded")
        _, dados = chamar(buscador, {"query": "perseguição"})
        self.assertEqual(dados["effective_strategy"], "lexical")
        self.assertTrue(dados["fallback_reason"])

    def test_strategy_names(self):
        self.assertEqual(normalizar_estrategia("bm25"), "lexical")
        with self.assertRaises(ValueError):
            normalizar_estrategia("inexistente")


if __name__ == "__main__":
    unittest.main()
