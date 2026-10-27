"""Mede a qualidade da recuperação contra o gabarito (avaliacao/gabarito.yaml).

Uso (na raiz deste repositório):
    .venv/Scripts/python avaliar.py                    # só perguntas com status "revisado"
    .venv/Scripts/python avaliar.py --incluir-rascunho # inclui os rascunhos (números provisórios)
    .venv/Scripts/python avaliar.py --detalhes         # mostra o ranking de cada pergunta

Métricas (por artigo; partes "-p1/-p2" de um mesmo artigo contam como um só):
    acerto@k   fração de perguntas com pelo menos um artigo relevante entre os k primeiros
    recall@k   fração dos artigos relevantes que aparecem entre os k primeiros
    MRR        média de 1/posição do primeiro artigo relevante (até a posição 10)
    nDCG@10    ordem do ranking, com relevante = 2 e parcial = 1

Perguntas sem_resposta não entram nessas médias: para elas o relatório mostra o escore
do primeiro resultado, que servirá para calibrar o limiar de recusa.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent
GABARITO = RAIZ / "avaliacao" / "gabarito.yaml"
RESULTADOS = RAIZ / "avaliacao" / "resultados"
KS = (1, 3, 5, 10)


def recuperadores():
    from busca_lexical import BuscaLexical
    return {"lexical": BuscaLexical}


def artigo(id_trecho):
    """'lei-11340-2006:art22-p2' -> 'lei-11340-2006:art22'"""
    return re.sub(r"-p\d+$", "", id_trecho)


def ranking_por_artigo(resultados):
    vistos, ordem = set(), []
    for r in resultados:
        a = artigo(r["id"])
        if a not in vistos:
            vistos.add(a)
            ordem.append((a, r["score"]))
    return ordem


def metricas(ordem, relevantes, parciais):
    ids = [a for a, _ in ordem]
    m = {}
    for k in KS:
        topo = set(ids[:k])
        m[f"acerto@{k}"] = float(bool(topo & relevantes))
        m[f"recall@{k}"] = len(topo & relevantes) / len(relevantes)
    pos = next((i for i, a in enumerate(ids[:10], 1) if a in relevantes), None)
    m["MRR"] = 1 / pos if pos else 0.0
    ganho = lambda a: 2 if a in relevantes else 1 if a in parciais else 0
    dcg = sum(ganho(a) / math.log2(i + 1) for i, a in enumerate(ids[:10], 1))
    ideal = sorted([2] * len(relevantes) + [1] * len(parciais), reverse=True)[:10]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    m["nDCG@10"] = dcg / idcg if idcg else 0.0
    return m


def media(linhas, chave):
    return sum(l[chave] for l in linhas) / len(linhas) if linhas else float("nan")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recuperador", default="lexical", choices=list(recuperadores()))
    parser.add_argument("--incluir-rascunho", action="store_true")
    parser.add_argument("--detalhes", action="store_true")
    args = parser.parse_args()

    perguntas = yaml.safe_load(GABARITO.read_text(encoding="utf-8"))["perguntas"]
    rascunhos = sum(1 for p in perguntas if p.get("status") != "revisado")
    if not args.incluir_rascunho:
        perguntas = [p for p in perguntas if p.get("status") == "revisado"]
    if not perguntas:
        sys.exit(f"Nenhuma pergunta revisada no gabarito ({rascunhos} em rascunho). "
                 "Use --incluir-rascunho para números provisórios.")

    corpus = json.loads((RAIZ / "corpus" / "manifesto.json").read_text(encoding="utf-8"))
    existentes = {artigo(json.loads(l)["id"]) for l in (RAIZ / "corpus" / "trechos.jsonl").open(encoding="utf-8")}
    busca = recuperadores()[args.recuperador]()

    com_resposta, sem_resposta, por_tipo = [], [], defaultdict(list)
    for p in perguntas:
        relevantes, parciais = set(p.get("relevantes") or []), set(p.get("parciais") or [])
        desconhecidos = (relevantes | parciais) - existentes
        if desconhecidos:
            sys.exit(f"{p['id']}: artigos que não existem no corpus: {sorted(desconhecidos)}")
        ordem = ranking_por_artigo(busca.search(p["pergunta"], limit=30))
        topo = ordem[0][1] if ordem else 0.0

        if p["tipo"] == "sem_resposta":
            sem_resposta.append({"id": p["id"], "pergunta": p["pergunta"], "score_topo": topo})
        else:
            linha = {"id": p["id"], "tipo": p["tipo"], "score_topo": topo, **metricas(ordem, relevantes, parciais)}
            com_resposta.append(linha)
            por_tipo[p["tipo"]].append(linha)

        if args.detalhes:
            marca = lambda a: "✔" if a in relevantes else "~" if a in parciais else " "
            print(f"\n{p['id']} [{p['tipo']}] {p['pergunta']}")
            print("   esperado: " + (", ".join(sorted(relevantes)) or "(nada: deve recusar)"))
            for i, (a, s) in enumerate(ordem[:5], 1):
                print(f"   {i}. {marca(a)} {s:6.2f} {a}")

    nomes = [f"acerto@{k}" for k in KS] + ["recall@5", "recall@10", "MRR", "nDCG@10"]
    print(f"\nRecuperador: {args.recuperador} · corpus {corpus['versao']} · "
          f"{len(com_resposta)} perguntas com resposta, {len(sem_resposta)} sem resposta"
          + (f" · ATENÇÃO: inclui {rascunhos} rascunhos" if args.incluir_rascunho else ""))
    print(f"\n{'':14}" + "".join(f"{n:>11}" for n in nomes))
    grupos = [("todas", com_resposta)] + sorted(por_tipo.items())
    for nome, linhas in grupos:
        print(f"{nome + f' ({len(linhas)})':14}" + "".join(f"{media(linhas, n):11.3f}" for n in nomes))

    if sem_resposta:
        com = sorted(l["score_topo"] for l in com_resposta)
        print("\nEscore do 1º resultado (para o limiar de recusa):")
        print(f"   com resposta: mínimo {com[0]:.2f} · mediana {com[len(com) // 2]:.2f}")
        for s in sem_resposta:
            print(f"   sem resposta: {s['score_topo']:.2f}  {s['pergunta']}")

    RESULTADOS.mkdir(parents=True, exist_ok=True)
    saida = RESULTADOS / f"{args.recuperador}-{corpus['versao']}.json"
    saida.write_text(json.dumps({
        "recuperador": args.recuperador, "corpus": corpus["versao"], "data": dt.date.today().isoformat(),
        "inclui_rascunho": args.incluir_rascunho,
        "medias": {nome: {n: media(linhas, n) for n in nomes} for nome, linhas in grupos},
        "perguntas": com_resposta, "sem_resposta": sem_resposta,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nResultados salvos em {saida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
