"""Servidor MCP com a ferramenta search_legal_sources.

O backend do Notas.ia monta este servidor com o prefixo "legal", então a LLM vê a ferramenta
como legal_search_legal_sources. O formato da resposta é o que agent/runtime.py lê.
"""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from legal_rag.busca import LIMITE_MAXIMO, Buscador

DESCRICAO = (
    "Busca trechos de leis brasileiras sobre violência doméstica e familiar contra a mulher "
    "(Lei Maria da Penha, Código Penal, leis correlatas e jurisprudência). Use para qualquer "
    "dúvida sobre direitos, crimes, medidas protetivas ou procedimentos. Escreva a consulta "
    "com as palavras da usuária ou com termos jurídicos. Devolve o texto oficial de cada "
    "trecho, com documento, seção e link."
)


def criar_servidor(buscador: Buscador) -> FastMCP:
    servidor = FastMCP("Notas.ia Legal RAG")

    @servidor.tool(name="search_legal_sources", description=DESCRICAO)
    def search_legal_sources(
        query: Annotated[str, Field(description="Dúvida ou situação a pesquisar", min_length=1)],
        limit: Annotated[int, Field(description="Quantos trechos devolver", ge=1,
                                    le=LIMITE_MAXIMO)] = 5,
    ) -> dict:
        return buscador.buscar(query, limit)

    @servidor.custom_route("/health/ready", methods=["GET"])
    async def pronto(_: Request) -> JSONResponse:
        # 200 também no fallback: a BM25 responde; o motivo aparece em fallback_reason
        return JSONResponse(buscador.pronto())

    @servidor.custom_route("/health/live", methods=["GET"])
    async def vivo(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return servidor
