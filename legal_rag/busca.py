"""Escolha da estratégia de busca, fallback para a BM25 e formato da resposta do MCP."""

import json
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:  # os módulos da busca ficam na raiz do repositório
    sys.path.insert(0, str(RAIZ))

import busca_densa  # noqa: E402
import busca_lexical  # noqa: E402

ESTRATEGIA_SALVA = RAIZ / "indice" / "estrategia.json"
PADRAO = "hibrida-bge-m3"
LIMITE_MAXIMO = 10


def estrategias():
    nomes = ["lexical"]
    for modelo in busca_densa.MODELOS:
        nomes += [f"densa-{modelo}", f"hibrida-{modelo}"]
    return nomes


def normalizar_estrategia(nome):
    nome = "lexical" if nome == "bm25" else nome  # nome usado no compose.yaml do notas-ia
    if nome not in estrategias():
        raise ValueError(f"Estratégia desconhecida: {nome}. Opções: {', '.join(estrategias())}")
    return nome


def versao_do_corpus():
    return busca_densa.versao_do_corpus()


def indexar(estrategia):
    """Gera o índice BM25 (sempre: é o fallback) e o denso, se a estratégia usar um."""
    estrategia = normalizar_estrategia(estrategia)
    busca_lexical.indexar()
    if estrategia != "lexical":
        busca_densa.indexar(estrategia.split("-", 1)[1])
    ESTRATEGIA_SALVA.write_text(json.dumps({"estrategia": estrategia, "corpus": versao_do_corpus()}),
                                encoding="utf-8")
    print(f"Estratégia {estrategia} pronta para o corpus {versao_do_corpus()}")


def estrategia_salva():
    if ESTRATEGIA_SALVA.exists():
        return json.loads(ESTRATEGIA_SALVA.read_text(encoding="utf-8"))["estrategia"]
    return PADRAO


class Buscador:
    """Carrega a estratégia pedida; se não der (índice ausente ou de outro corpus, modelo que
    não carrega), usa a BM25 e guarda o motivo em fallback_reason."""

    def __init__(self, estrategia=None):
        self.pedida = normalizar_estrategia(estrategia or estrategia_salva())
        self.trava = threading.Lock()  # o modelo de embeddings não é usado em paralelo
        self.fallback_reason = None
        if not busca_lexical.INDICE.exists():
            busca_lexical.indexar()
        self.busca, self.efetiva = None, self.pedida
        if self.pedida != "lexical":
            tipo, modelo = self.pedida.split("-", 1)
            try:
                self.busca = (busca_densa.BuscaHibrida(modelo) if tipo == "hibrida"
                              else busca_densa.BuscaDensa(modelo))
            except (SystemExit, Exception) as erro:  # BuscaDensa usa sys.exit quando falta índice
                self.fallback_reason = f"{self.pedida} indisponível: {erro}"
        if self.busca is None:
            self.busca, self.efetiva = busca_lexical.BuscaLexical(), "lexical"

    def pronto(self):
        return {"status": "degraded" if self.fallback_reason else "ok",
                "requested_strategy": self.pedida, "effective_strategy": self.efetiva,
                "fallback_reason": self.fallback_reason, "corpus_version": versao_do_corpus()}

    def buscar(self, consulta, limit=5):
        limit = max(1, min(int(limit), LIMITE_MAXIMO))
        with self.trava:
            trechos = self.busca.search(consulta, limit)
        return {
            "corpus_version": versao_do_corpus(),
            "effective_strategy": self.efetiva,
            "fallback_reason": self.fallback_reason,
            # só o texto oficial: as palavras-chave do enriquecimento nunca vão para a LLM
            "results": [{
                "chunk_id": t["id"],
                "document": t["documento"],
                "section": t["secao"],
                "url": t["url"],
                "verified_at": t.get("verificado_em"),
                "text": t["texto"],
            } for t in trechos],
        }
