"""Busca densa (embeddings + FAISS) e busca híbrida (BM25 + densa, por RRF).

Uso (na raiz deste repositório):
    .venv/Scripts/python busca_densa.py --indexar --modelo e5-small
    .venv/Scripts/python busca_densa.py "meu ex fica me seguindo" --modelo e5-small
    .venv/Scripts/python busca_densa.py "meu ex fica me seguindo" --modelo e5-small --hibrida

Na primeira vez, o modelo é baixado do Hugging Face (e5-small ~0,5 GB, bge-m3 ~2,3 GB).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent
TRECHOS = RAIZ / "corpus" / "trechos.jsonl"
INDICES = RAIZ / "indice"

# nome curto -> (modelo no Hugging Face, prefixo da consulta, prefixo do trecho)
MODELOS = {
    "e5-small": ("intfloat/multilingual-e5-small", "query: ", "passage: "),
    "e5-base": ("intfloat/multilingual-e5-base", "query: ", "passage: "),
    "bge-m3": ("BAAI/bge-m3", "", ""),
}


def texto_do_trecho(t):
    """O que vira embedding: documento e seção dão contexto ao texto do artigo."""
    return f"{t['documento']}, {t['secao']}. {t['contexto']}\n{t['texto']}"


def carregar_modelo(nome):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODELOS[nome][0], device="cpu")


def versao_do_corpus():
    return json.loads((RAIZ / "corpus" / "manifesto.json").read_text(encoding="utf-8"))["versao"]


def indexar(nome):
    import faiss
    trechos = [json.loads(l) for l in TRECHOS.read_text(encoding="utf-8").splitlines()]
    modelo = carregar_modelo(nome)
    prefixo = MODELOS[nome][2]
    vetores = modelo.encode([prefixo + texto_do_trecho(t) for t in trechos], batch_size=8,
                            normalize_embeddings=True, show_progress_bar=True)
    indice = faiss.IndexFlatIP(vetores.shape[1])  # produto interno de vetores normalizados = cosseno
    indice.add(np.asarray(vetores, dtype="float32"))
    INDICES.mkdir(parents=True, exist_ok=True)
    faiss.write_index(indice, str(INDICES / f"densa-{nome}.faiss"))
    (INDICES / f"densa-{nome}.json").write_text(json.dumps({
        "modelo": MODELOS[nome][0], "corpus": versao_do_corpus(), "ids": [t["id"] for t in trechos],
    }, ensure_ascii=False), encoding="utf-8")
    print(f"{len(trechos)} trechos indexados com {MODELOS[nome][0]} ({vetores.shape[1]} dimensões)")


class BuscaDensa:
    def __init__(self, nome="e5-small"):
        import faiss
        meta_caminho = INDICES / f"densa-{nome}.json"
        if not meta_caminho.exists():
            sys.exit(f"Índice denso não encontrado. Rode antes: busca_densa.py --indexar --modelo {nome}")
        meta = json.loads(meta_caminho.read_text(encoding="utf-8"))
        if meta["corpus"] != versao_do_corpus():
            sys.exit(f"Índice denso ({meta['corpus']}) é de outra versão do corpus. Reindexe com --indexar.")
        self.nome = nome
        self.indice = faiss.read_index(str(INDICES / f"densa-{nome}.faiss"))
        self.ids = meta["ids"]
        self.trechos = {json.loads(l)["id"]: json.loads(l) for l in TRECHOS.read_text(encoding="utf-8").splitlines()}
        self.modelo = carregar_modelo(nome)

    def search(self, consulta, limit=5):
        vetor = self.modelo.encode([MODELOS[self.nome][1] + consulta], normalize_embeddings=True)
        scores, posicoes = self.indice.search(np.asarray(vetor, dtype="float32"), min(limit, len(self.ids)))
        return [{**self.trechos[self.ids[p]], "score": float(s)} for s, p in zip(scores[0], posicoes[0]) if p >= 0]


class BuscaHibrida:
    """Reciprocal rank fusion: soma 1/(k + posição) de cada lista. Não depende da escala dos escores."""

    def __init__(self, nome="e5-small", k=60, candidatos=30):
        from busca_lexical import BuscaLexical
        self.lexical, self.densa = BuscaLexical(), BuscaDensa(nome)
        self.k, self.candidatos = k, candidatos

    def search(self, consulta, limit=5):
        fusao, por_id = {}, {}
        for lista in (self.lexical.search(consulta, self.candidatos), self.densa.search(consulta, self.candidatos)):
            for posicao, r in enumerate(lista, 1):
                fusao[r["id"]] = fusao.get(r["id"], 0.0) + 1 / (self.k + posicao)
                por_id.setdefault(r["id"], r)
        ordem = sorted(fusao, key=fusao.get, reverse=True)[:limit]
        return [{**por_id[i], "score": fusao[i]} for i in ordem]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("consulta", nargs="?")
    parser.add_argument("--modelo", default="e5-small", choices=list(MODELOS))
    parser.add_argument("--indexar", action="store_true")
    parser.add_argument("--hibrida", action="store_true")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    if args.indexar:
        indexar(args.modelo)
    if args.consulta:
        busca = BuscaHibrida(args.modelo) if args.hibrida else BuscaDensa(args.modelo)
        for r in busca.search(args.consulta, args.k):
            print(f"{r['score']:6.3f}  {r['documento']}, {r['secao']}")
            print("        " + r["texto"].replace("\n", " ")[:160])
