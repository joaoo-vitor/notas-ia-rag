"""Adaptador local que expõe as buscas simples como ferramenta do agente."""
from __future__ import annotations

from pathlib import Path

from busca_densa import BuscaDensa, BuscaHibrida, versao_do_corpus
from busca_lexical import BuscaLexical


class LegalSearchTool:
    def __init__(self, raiz: str | Path | None = None):
        self.raiz = Path(raiz or Path(__file__).resolve().parent)
        self.lexical = BuscaLexical(self.raiz)
        self.densa = None
        self.hibrida = None

    def _load_dense(self) -> bool:
        """Carrega o encoder apenas quando a consulta realmente precisa dele.

        A busca lexical continua disponível em máquinas sem o índice/encoder denso;
        iniciar a API não deve baixar nem ocupar memória por causa dessa opção.
        """
        if self.hibrida:
            return True
        try:
            self.densa = BuscaDensa("e5-small", self.raiz)
            self.hibrida = BuscaHibrida("e5-small", raiz=self.raiz)
            return True
        except SystemExit:
            self.densa = self.hibrida = None
            return False

    def search(self, query: str, strategy: str | None = None, top_k: int | None = None) -> dict:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query deve conter texto")
        if len(query) > 1000:
            raise ValueError("query deve ter no máximo 1000 caracteres")
        requested = strategy or "auto"
        if requested not in {"auto", "bm25", "dense", "hybrid"}:
            raise ValueError("strategy deve ser bm25, dense, hybrid ou auto")
        limit = 5 if top_k is None else int(top_k)
        if not 1 <= limit <= 10:
            raise ValueError("top_k deve estar entre 1 e 10")

        effective, fallback = requested, None
        if requested == "auto":
            effective = "hybrid" if self._load_dense() else "bm25"
            fallback = None if self.hibrida else "Índice denso indisponível; busca BM25 utilizada."
        if effective == "bm25":
            results = self.lexical.search(query, limit)
        elif effective == "dense":
            if not self._load_dense():
                raise RuntimeError("Índice denso indisponível")
            results = self.densa.search(query, limit)
        else:
            if not self._load_dense():
                raise RuntimeError("Busca híbrida indisponível")
            results = self.hibrida.search(query, limit)

        return {
            "query": query, "requested_strategy": requested, "effective_strategy": effective,
            "fallback_reason": fallback, "corpus_version": versao_do_corpus(self.raiz),
            "results": [self._source(item) for item in results],
        }

    async def call(self, arguments: dict) -> dict:
        # A busca local é curta e mantém o módulo independente de executores e de
        # sincronização extra. Se o volume crescer, o registry poderá mover apenas
        # este handler para um worker sem alterar o contrato da ferramenta.
        return self.search(**arguments)

    @staticmethod
    def _source(item: dict) -> dict:
        return {
            "chunk_id": item["id"], "document": item["documento"], "section": item["secao"],
            "context": item["contexto"], "text": item["texto"], "organization": item["orgao"],
            "source_type": item["tipo"], "jurisdiction": item["jurisdicao"],
            "locality": item.get("localidade"), "url": item["url"],
            "published_at": item["publicado_em"], "verified_at": item["verificado_em"],
            "score": float(item["score"]), "expressions": item.get("expressions", []),
            "matched_description": item.get("matched_description"),
        }
