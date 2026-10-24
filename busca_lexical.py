"""Busca lexical (BM25) sobre o corpus, com SQLite FTS5.

Uso (na raiz do repositório):
    rag/.venv/Scripts/python rag/busca_lexical.py --indexar
    rag/.venv/Scripts/python rag/busca_lexical.py "o que é violência patrimonial?"
"""

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
TRECHOS = RAIZ / "corpus" / "trechos.jsonl"
INDICE = RAIZ / "indice" / "lexical.sqlite"

# Palavras sem valor de busca; sem elas, perguntas em linguagem natural casam com quase tudo.
STOPWORDS = set("""
a à ao aos as às até com como da das de dela dele do dos e é ela ele em entre era essa esse esta este eu foi
for há isso isto já la lhe mais mas me meu minha muito na nas não nem no nos nós o os ou para pela pelas
pelo pelos por qual quais quando que quem se sem ser seu sua são só também te tem tenho ter tu um uma uns
umas vai você vou posso pode fazer faz sobre quero saber tá ta to tô pra pro
""".split())


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def termos(consulta):
    palavras = re.findall(r"\w+", normalizar(consulta))
    return [p for p in palavras if p not in {normalizar(s) for s in STOPWORDS} and len(p) > 1]


def indexar():
    INDICE.parent.mkdir(parents=True, exist_ok=True)
    if INDICE.exists():
        INDICE.unlink()
    con = sqlite3.connect(INDICE)
    con.execute("CREATE TABLE trechos (id TEXT PRIMARY KEY, dados TEXT)")
    # remove_diacritics: "agressão" e "agressao" casam; prefix: permite busca por prefixo (radical)
    con.execute("""CREATE VIRTUAL TABLE fts USING fts5(
        id UNINDEXED, texto, contexto, documento,
        tokenize = 'unicode61 remove_diacritics 2', prefix = '4 5 6')""")
    n = 0
    for linha in TRECHOS.read_text(encoding="utf-8").splitlines():
        t = json.loads(linha)
        con.execute("INSERT INTO trechos VALUES (?, ?)", (t["id"], linha))
        con.execute("INSERT INTO fts VALUES (?, ?, ?, ?)", (t["id"], t["texto"], t["contexto"], t["documento"]))
        n += 1
    con.commit()
    con.close()
    print(f"{n} trechos indexados em {INDICE}")


class BuscaLexical:
    """Mesmo formato de saída que os outros recuperadores: lista de trechos com 'score'."""

    def __init__(self, caminho=INDICE):
        if not Path(caminho).exists():
            sys.exit("Índice não encontrado. Rode antes: busca_lexical.py --indexar")
        self.con = sqlite3.connect(caminho)

    def search(self, consulta, limit=5, radical=True):
        lista = termos(consulta)
        if not lista:
            return []
        # OR entre os termos: perguntas longas raramente têm todas as palavras no mesmo artigo.
        # radical: "agredida" também acha "agressão"... só quando os 5 primeiros caracteres coincidem.
        expressao = " OR ".join(f'"{p[:5]}"*' if radical and len(p) > 5 else f'"{p}"' for p in lista)
        linhas = self.con.execute(
            """SELECT fts.id, bm25(fts, 1.0, 0.3, 0.2) AS score, trechos.dados
               FROM fts JOIN trechos ON trechos.id = fts.id
               WHERE fts MATCH ? ORDER BY score LIMIT ?""",
            (expressao, limit),
        ).fetchall()
        # bm25() do SQLite é negativo (menor = melhor); invertido para "maior = melhor"
        return [{**json.loads(dados), "score": -score} for _, score, dados in linhas]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("consulta", nargs="?")
    parser.add_argument("--indexar", action="store_true")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()
    if args.indexar:
        indexar()
    if args.consulta:
        for r in BuscaLexical().search(args.consulta, args.k):
            print(f"{r['score']:6.2f}  {r['documento']}, {r['secao']}")
            print("        " + r["texto"].replace("\n", " ")[:160])
