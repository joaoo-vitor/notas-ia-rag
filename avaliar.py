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

As mesmas métricas também são calculadas por trecho. Artigos não divididos são
resolvidos automaticamente. Para artigos divididos, o gabarito deve indicar
"trechos_relevantes" e/ou "trechos_parciais"; perguntas sem essa anotação ficam
pendentes apenas na avaliação por trecho.

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
    """Nome -> função que cria o recuperador (importações pesadas só quando usado)."""
    from busca_lexical import BuscaLexical
    opcoes = {"lexical": BuscaLexical}
    for modelo in ("e5-small", "e5-base", "bge-m3"):
        def densa(m=modelo):
            from busca_densa import BuscaDensa
            return BuscaDensa(m)
        def hibrida(m=modelo):
            from busca_densa import BuscaHibrida
            return BuscaHibrida(m)
        opcoes[f"densa-{modelo}"] = densa
        opcoes[f"hibrida-{modelo}"] = hibrida
    return opcoes


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


def ranking_por_trecho(resultados):
    return [(r["id"], r["score"]) for r in resultados]


def mapa_trechos(caminho):
    """Artigo -> ids de seus trechos, na ordem do corpus."""
    mapa = defaultdict(list)
    for linha in caminho.open(encoding="utf-8"):
        id_trecho = json.loads(linha)["id"]
        mapa[artigo(id_trecho)].append(id_trecho)
    return mapa


def rotulos_por_trecho(pergunta, trechos_por_artigo):
    """Converte rótulos por artigo em rótulos por trecho sem inventar a parte correta.

    Para artigo com uma única parte, o próprio id do artigo é também o id do trecho.
    Para artigo dividido, exige seleção explícita no gabarito.
    """
    relevantes_artigo = set(pergunta.get("relevantes") or [])
    parciais_artigo = set(pergunta.get("parciais") or [])
    relevantes_explicitos = set(pergunta.get("trechos_relevantes") or [])
    parciais_explicitos = set(pergunta.get("trechos_parciais") or [])
    todos_explicitos = relevantes_explicitos | parciais_explicitos
    existentes = {t for partes in trechos_por_artigo.values() for t in partes}
    desconhecidos = todos_explicitos - existentes
    if desconhecidos:
        raise ValueError(f"trechos que não existem no corpus: {sorted(desconhecidos)}")

    fora_do_artigo = {
        t for t in relevantes_explicitos if artigo(t) not in relevantes_artigo
    } | {
        t for t in parciais_explicitos if artigo(t) not in parciais_artigo
    }
    if fora_do_artigo:
        raise ValueError(
            "trechos específicos sem o artigo correspondente em relevantes/parciais: "
            f"{sorted(fora_do_artigo)}"
        )

    relevantes, parciais, pendentes = set(), set(), []
    for rotulos_artigo, explicitos, destino in (
        (relevantes_artigo, relevantes_explicitos, relevantes),
        (parciais_artigo, parciais_explicitos, parciais),
    ):
        for id_artigo in rotulos_artigo:
            partes = trechos_por_artigo[id_artigo]
            if len(partes) == 1:
                destino.add(partes[0])
                continue
            escolhidos = {t for t in explicitos if artigo(t) == id_artigo}
            if escolhidos:
                destino.update(escolhidos)
            else:
                pendentes.append(id_artigo)
    return relevantes, parciais, sorted(set(pendentes))


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


def imprimir_tabela(titulo, linhas, por_tipo, nomes):
    print(f"\n{titulo} ({len(linhas)} perguntas)")
    print(f"{'':14}" + "".join(f"{n:>11}" for n in nomes))
    grupos = [("todas", linhas)] + sorted(por_tipo.items())
    for nome, grupo in grupos:
        print(f"{nome + f' ({len(grupo)})':14}" + "".join(
            f"{media(grupo, n):11.3f}" for n in nomes
        ))
    return grupos


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
    caminho_trechos = RAIZ / "corpus" / "trechos.jsonl"
    trechos_por_artigo = mapa_trechos(caminho_trechos)
    existentes = set(trechos_por_artigo)
    busca = recuperadores()[args.recuperador]()

    com_resposta, sem_resposta, por_tipo = [], [], defaultdict(list)
    com_resposta_trecho, por_tipo_trecho, pendentes_trecho = [], defaultdict(list), []
    for p in perguntas:
        relevantes, parciais = set(p.get("relevantes") or []), set(p.get("parciais") or [])
        desconhecidos = (relevantes | parciais) - existentes
        if desconhecidos:
            sys.exit(f"{p['id']}: artigos que não existem no corpus: {sorted(desconhecidos)}")
        resultados = busca.search(p["pergunta"], limit=30)
        ordem = ranking_por_artigo(resultados)
        ordem_trecho = ranking_por_trecho(resultados)
        topo = ordem[0][1] if ordem else 0.0

        if p["tipo"] == "sem_resposta":
            sem_resposta.append({"id": p["id"], "pergunta": p["pergunta"], "score_topo": topo})
        else:
            linha = {"id": p["id"], "tipo": p["tipo"], "score_topo": topo, **metricas(ordem, relevantes, parciais)}
            try:
                relevantes_trecho, parciais_trecho, pendentes = rotulos_por_trecho(
                    p, trechos_por_artigo
                )
            except ValueError as exc:
                sys.exit(f"{p['id']}: {exc}")
            if pendentes:
                linha["metricas_trecho"] = None
                pendentes_trecho.append({"id": p["id"], "artigos": pendentes})
            else:
                linha_trecho = {
                    "id": p["id"], "tipo": p["tipo"],
                    **metricas(ordem_trecho, relevantes_trecho, parciais_trecho),
                }
                linha["metricas_trecho"] = {
                    k: v for k, v in linha_trecho.items() if k not in {"id", "tipo"}
                }
                com_resposta_trecho.append(linha_trecho)
                por_tipo_trecho[p["tipo"]].append(linha_trecho)
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
    grupos = imprimir_tabela("Avaliação por artigo", com_resposta, por_tipo, nomes)
    grupos_trecho = imprimir_tabela(
        "Avaliação por trecho", com_resposta_trecho, por_tipo_trecho, nomes
    )
    if pendentes_trecho:
        print(f"\nTrechos pendentes: {len(pendentes_trecho)} pergunta(s). "
              "Preencha trechos_relevantes/trechos_parciais para artigos divididos:")
        for item in pendentes_trecho:
            print(f"   {item['id']}: {', '.join(item['artigos'])}")

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
        "medias_trecho": {
            nome: {n: media(linhas, n) for n in nomes} for nome, linhas in grupos_trecho
        },
        "cobertura_trecho": {
            "avaliadas": len(com_resposta_trecho),
            "total_com_resposta": len(com_resposta),
            "pendentes": pendentes_trecho,
        },
        "perguntas": com_resposta, "sem_resposta": sem_resposta,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nResultados salvos em {saida.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
