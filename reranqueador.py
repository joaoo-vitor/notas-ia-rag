"""Reranqueador: reordena os candidatos de uma busca lendo pergunta e trecho juntos.

A busca densa compara vetores calculados separadamente para a pergunta e para o trecho.
O reranqueador (cross-encoder) lê os dois ao mesmo tempo, então percebe se o trecho
responde a pergunta, e não só se fala de assunto parecido. É mais lento, por isso só
reordena os primeiros candidatos de outra busca.

Uso (na raiz deste repositório):
    .venv/Scripts/python reranqueador.py "ele pegou meus documentos"
    .venv/Scripts/python reranqueador.py "ele pegou meus documentos" --reranqueador mmarco --base uniao

Na primeira vez, o modelo é baixado do Hugging Face (bge-v2-m3 ~2,3 GB, mmarco ~0,5 GB).
"""

import argparse
import sys
import time

from busca_densa import BuscaDensa, texto_do_trecho

# nome curto -> modelo no Hugging Face
RERANQUEADORES = {
    "bge-v2-m3": "BAAI/bge-reranker-v2-m3",
    "mmarco": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
}

# De onde vêm os candidatos:
#   densa   os primeiros da BGE-M3 densa
#   uniao   os primeiros da BGE-M3 densa somados aos da BM25 (sem repetir); a BM25 acerta
#           perguntas que a densa erra, e o reranqueador decide entre as duas listas
BASES = ("densa", "uniao")


class BuscaReranqueada:
    def __init__(self, reranqueador="bge-v2-m3", base="densa", candidatos=30):
        from sentence_transformers import CrossEncoder
        if base not in BASES:
            raise ValueError(f"base desconhecida: {base}")
        self.densa = BuscaDensa("bge-m3")
        self.lexical = None
        if base == "uniao":
            from busca_lexical import BuscaLexical
            self.lexical = BuscaLexical()
        self.candidatos = candidatos
        self.modelo = CrossEncoder(RERANQUEADORES[reranqueador], device="cpu", max_length=512)

    def candidatos_de(self, consulta):
        lista = self.densa.search(consulta, self.candidatos)
        if self.lexical:
            vistos = {r["id"] for r in lista}
            lista += [r for r in self.lexical.search(consulta, self.candidatos) if r["id"] not in vistos]
        return lista

    def search(self, consulta, limit=5):
        candidatos = self.candidatos_de(consulta)
        if not candidatos:
            return []
        # em lotes de trechos de tamanho parecido: o lote é completado até o maior texto,
        # e misturar trechos curtos com longos desperdiça processamento (CPU)
        candidatos = sorted(candidatos, key=lambda r: len(texto_do_trecho(r)))
        scores = self.modelo.predict([(consulta, texto_do_trecho(r)) for r in candidatos],
                                     batch_size=8, show_progress_bar=False)
        ordem = sorted(zip(candidatos, scores), key=lambda par: par[1], reverse=True)[:limit]
        return [{**r, "score": float(s)} for r, s in ordem]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("consulta")
    parser.add_argument("--reranqueador", default="bge-v2-m3", choices=list(RERANQUEADORES))
    parser.add_argument("--base", default="densa", choices=BASES)
    parser.add_argument("--candidatos", type=int, default=30)
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    busca = BuscaReranqueada(args.reranqueador, args.base, args.candidatos)
    inicio = time.perf_counter()
    resultados = busca.search(args.consulta, args.k)
    print(f"({time.perf_counter() - inicio:.1f} s)")
    for r in resultados:
        print(f"{r['score']:6.3f}  {r['documento']}, {r['secao']}")
        print("        " + r["texto"].replace("\n", " ")[:160])
