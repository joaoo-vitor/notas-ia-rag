"""Busca lexical (BM25) sobre o corpus, com SQLite FTS5.

Uso (na raiz deste repositório):
    .venv/Scripts/python busca_lexical.py --indexar
    .venv/Scripts/python busca_lexical.py "o que é violência patrimonial?"
"""

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from recuperacao import carregar_trechos, versao_indice

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

# A ordem acompanha todas as colunas da tabela FTS, inclusive o id UNINDEXED:
# id=0, texto=1, contexto=0.3, documento=0.2, expressions=1.2.
PESOS_BM25 = (0.0, 1.0, 0.3, 0.2, 1.2)


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


def termos(consulta):
    palavras = re.findall(r"\w+", normalizar(consulta))
    return [p for p in palavras if p not in {normalizar(s) for s in STOPWORDS} and len(p) > 1]


def indexar(raiz=RAIZ):
    indice = Path(raiz) / "indice" / "lexical.sqlite"
    indice.parent.mkdir(parents=True, exist_ok=True)
    if indice.exists():
        indice.unlink()
    con = sqlite3.connect(indice)
    con.execute("CREATE TABLE trechos (id TEXT PRIMARY KEY, dados TEXT)")
    con.execute("CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT)")
    # remove_diacritics: "agressão" e "agressao" casam; prefix: permite busca por prefixo (radical)
    con.execute("""CREATE VIRTUAL TABLE fts USING fts5(
        id UNINDEXED, texto, contexto, documento, expressions,
        tokenize = 'unicode61 remove_diacritics 2', prefix = '4 5 6')""")
    n = 0
    for t in carregar_trechos(Path(raiz)):
        linha = json.dumps(t, ensure_ascii=False)
        con.execute("INSERT INTO trechos VALUES (?, ?)", (t["id"], linha))
        con.execute("INSERT INTO fts VALUES (?, ?, ?, ?, ?)",
                    (t["id"], t["texto"], t["contexto"], t["documento"], " ; ".join(t["expressions"])))
        n += 1
    con.execute("INSERT INTO meta VALUES (?, ?)", ("versao", versao_indice(Path(raiz))))
    con.commit()
    con.close()
    print(f"{n} trechos indexados em {indice}")


class BuscaLexical:
    """Mesmo formato de saída que os outros recuperadores: lista de trechos com 'score'."""

    def __init__(self, raiz=RAIZ):
        self.raiz = Path(raiz)
        caminho = self.raiz / "indice" / "lexical.sqlite"
        if not caminho.exists():
            sys.exit("Índice não encontrado. Rode antes: busca_lexical.py --indexar")
        with sqlite3.connect(caminho) as con:
            linha = con.execute("SELECT valor FROM meta WHERE chave='versao'").fetchone()
        if not linha or linha[0] != versao_indice(self.raiz):
            sys.exit("Índice lexical é de outra versão do corpus. Reindexe com --indexar.")
        self.con = sqlite3.connect(caminho)

    def search(self, consulta, limit=5, radical=True):
        lista = termos(consulta)
        if not lista:
            return []
        # OR entre os termos: perguntas longas raramente têm todas as palavras no mesmo artigo.
        # radical: "agredida" também acha "agressão"... só quando os 5 primeiros caracteres coincidem.
        expressao = " OR ".join(f'"{p[:5]}"*' if radical and len(p) > 5 else f'"{p}"' for p in lista)
        pesos = ", ".join(str(p) for p in PESOS_BM25)
        linhas = self.con.execute(
            f"""SELECT fts.id, bm25(fts, {pesos}) AS score, trechos.dados
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
