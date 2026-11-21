"""Métricas automáticas das respostas geradas, medidas contra o gabarito.

Não precisa de respostas de referência: usa os artigos relevantes de cada pergunta para
julgar as citações e o tipo sem_resposta para julgar as recusas.

Por pergunta (média com intervalo de confiança de 95% por bootstrap):
  recusa_correta       sem_resposta: o modelo recusou (sem_base)
  recusa_indevida      com resposta: o modelo recusou mesmo assim
  citou_relevante      com resposta e não recusada: citou ao menos um artigo relevante
  precisao_citacao     artigos citados que estão em relevantes ou parciais / artigos citados
  precisao_estrita     o mesmo, contando só relevantes
  cobertura_citacao    artigos relevantes citados / artigos relevantes
  citacao_invalida     citou um número de trecho que não foi fornecido
  contexto_relevante   o contexto fornecido tinha algum artigo relevante (qualidade da busca)
Agregado: precisão, cobertura e F1 da recusa (a classe positiva é "recusar").

Desfecho de cada pergunta com resposta, para separar erro da busca de erro da geração:
  ok               citou um artigo relevante
  erro_geracao     o contexto tinha o artigo relevante, mas a resposta não o citou
  erro_busca       o contexto não tinha artigo relevante (e o modelo respondeu mesmo assim)
  recusa_indevida  recusou uma pergunta que a base responde

Uso (na raiz deste repositório):
    .venv/Scripts/python avaliar_geracao.py --modelo gemini-3.5-flash
    .venv/Scripts/python avaliar_geracao.py --modelo gemini-3.5-flash --so-revisadas
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from avaliar import artigo
from geracao import CONDICOES, SAIDA, caminho_contextos, caminho_respostas, carregar_gabarito

N_BOOTSTRAP = 2000
TIPOS = ("direta", "coloquial", "adversa", "sem_resposta")
METRICAS = ("recusa_correta", "recusa_indevida", "citou_relevante", "precisao_citacao",
            "precisao_estrita", "cobertura_citacao", "citacao_invalida", "contexto_relevante")

# --- citações escritas por extenso (condição sem-busca) ------------------------------------

DOCUMENTOS = (
    (re.compile(r"11\.?340|maria da penha", re.I), "lei-11340-2006"),
    (re.compile(r"14\.?149", re.I), "lei-14149-2021"),
    (re.compile(r"14\.?188", re.I), "lei-14188-2021"),
    (re.compile(r"1\.?973", re.I), "decreto-1973-1996"),
    (re.compile(r"c[óo]digo penal|2\.?848|\bCP\b", re.I), "codigo-penal"),
    (re.compile(r"constitui", re.I), "constituicao-1988"),
)
ARTIGOS = re.compile(r"\bart(?:igo)?s?\.?\s*((?:\d+(?:-[A-Z])?[º°o]?(?:\s*(?:,|e)\s*)?)+)", re.I)
NUMERO = re.compile(r"(\d+)(?:-([A-Z]))?")


def artigos_por_extenso(textos):
    """["Lei 11.340/2006, art. 7", "Código Penal, arts. 129 e 147"] -> ids de artigo do corpus.
    Leis fora do corpus são ignoradas (não há como julgá-las com o gabarito)."""
    ids = []
    for texto in textos:
        documento = next((d for padrao, d in DOCUMENTOS if padrao.search(texto)), None)
        if not documento:
            continue
        for grupo in ARTIGOS.findall(texto):
            for numero, letra in NUMERO.findall(grupo):
                ids.append(f"{documento}:art{numero}" + (f"-{letra.upper()}" if letra else ""))
    return list(dict.fromkeys(ids))


# --- métricas por pergunta -----------------------------------------------------------------

def medir(pergunta, resposta, contexto):
    """Métricas de uma resposta. None = não se aplica a esta pergunta."""
    m = dict.fromkeys(METRICAS)
    relevantes = set(pergunta.get("relevantes") or [])
    aceitos = relevantes | set(pergunta.get("parciais") or [])
    recusou = resposta["sem_base"]
    if "citacoes" in resposta:
        citados = list(dict.fromkeys(artigo(t) for t in resposta["citacoes"]))
        m["citacao_invalida"] = float(bool(resposta["citacoes_invalidas"]))
    else:
        citados = artigos_por_extenso(resposta.get("artigos_citados", []))

    if not relevantes:  # sem_resposta
        m["recusa_correta"] = float(recusou)
        return m, ("recusa_correta" if recusou else "respondeu_sem_base")

    m["recusa_indevida"] = float(recusou)
    if contexto is not None:
        m["contexto_relevante"] = float(any(artigo(t) in relevantes for t in contexto))
    if recusou:
        return m, "recusa_indevida"
    m["citou_relevante"] = float(bool(relevantes & set(citados)))
    m["cobertura_citacao"] = len(relevantes & set(citados)) / len(relevantes)
    if citados:
        m["precisao_citacao"] = sum(c in aceitos for c in citados) / len(citados)
        m["precisao_estrita"] = sum(c in relevantes for c in citados) / len(citados)
    if m["citou_relevante"]:
        desfecho = "ok"
    elif contexto is None:
        desfecho = "sem_citacao_relevante"
    elif m["contexto_relevante"]:
        desfecho = "erro_geracao"
    else:
        desfecho = "erro_busca"
    return m, desfecho


def f1_recusa(linhas):
    vp = sum(1 for l in linhas if l["sem_resposta"] and l["recusou"])
    fp = sum(1 for l in linhas if not l["sem_resposta"] and l["recusou"])
    fn = sum(1 for l in linhas if l["sem_resposta"] and not l["recusou"])
    precisao = vp / (vp + fp) if vp + fp else 0.0
    cobertura = vp / (vp + fn) if vp + fn else 0.0
    f1 = 2 * precisao * cobertura / (precisao + cobertura) if precisao + cobertura else 0.0
    return {"precisao": precisao, "cobertura": cobertura, "f1": f1}


# --- bootstrap -----------------------------------------------------------------------------

def intervalo(valores, sorteios):
    """Média e IC 95% de uma métrica por pergunta; ignora as perguntas em que não se aplica."""
    v = np.array([np.nan if x is None else x for x in valores], dtype=float)
    if np.all(np.isnan(v)):
        return None
    medias = np.nanmean(v[sorteios], axis=1)
    return {"media": float(np.nanmean(v)), "ic95": [float(np.nanpercentile(medias, 2.5)),
                                                    float(np.nanpercentile(medias, 97.5))],
            "n": int(np.sum(~np.isnan(v)))}


def intervalo_f1(linhas, sorteios):
    pontos = [f1_recusa([linhas[i] for i in s])["f1"] for s in sorteios]
    return {**f1_recusa(linhas), "f1_ic95": [float(np.percentile(pontos, 2.5)),
                                             float(np.percentile(pontos, 97.5))]}


# --- principal -----------------------------------------------------------------------------

def carregar(condicao, modelo, perguntas):
    arquivo = caminho_respostas(condicao, modelo)
    if not arquivo.exists():
        return None
    respostas = {r["id"]: r for r in map(json.loads, arquivo.read_text(encoding="utf-8").splitlines())}
    contextos = None
    if condicao != "sem-busca":
        contextos = json.loads(caminho_contextos(condicao).read_text(encoding="utf-8"))["perguntas"]
    linhas = []
    for p in perguntas:
        r = respostas.get(p["id"])
        if r is None:
            continue
        ctx = None if contextos is None else contextos[p["id"]]["trechos"]
        metricas, desfecho = medir(p, r, ctx)
        linhas.append({"id": p["id"], "tipo": p["tipo"], "sem_resposta": not p.get("relevantes"),
                       "recusou": r["sem_base"], "desfecho": desfecho, **metricas})
    return linhas


def fmt(item):
    if item is None:
        return "     —      "
    return f"{item['media']:.3f} [{item['ic95'][0]:.2f}–{item['ic95'][1]:.2f}]"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelo", required=True)
    parser.add_argument("--so-revisadas", action="store_true", help="só perguntas com status revisado")
    parser.add_argument("--semente", type=int, default=13)
    parser.add_argument("--condicoes", nargs="*", choices=CONDICOES, default=list(CONDICOES),
                        help="condições a comparar (padrão: todas as que tiverem respostas)")
    args = parser.parse_args()

    perguntas, versao_gabarito = carregar_gabarito()
    if args.so_revisadas:
        perguntas = [p for p in perguntas if p.get("status") == "revisado"]
    por_condicao = {c: carregar(c, args.modelo, perguntas) for c in args.condicoes}
    por_condicao = {c: l for c, l in por_condicao.items() if l}
    if not por_condicao:
        sys.exit(f"Nenhuma resposta de {args.modelo} em {SAIDA}")

    # para comparar condições, usa só as perguntas respondidas em todas
    comuns = set.intersection(*({l["id"] for l in linhas} for linhas in por_condicao.values()))
    for c in por_condicao:
        por_condicao[c] = [l for l in por_condicao[c] if l["id"] in comuns]
    n = len(comuns)
    rng = np.random.default_rng(args.semente)
    sorteios = rng.integers(0, n, size=(N_BOOTSTRAP, n))  # os mesmos para todas as condições

    resultado = {"modelo": args.modelo, "gabarito": versao_gabarito, "perguntas": n,
                 "so_revisadas": args.so_revisadas, "condicoes": {}}
    print(f"{args.modelo}: {n} perguntas em comum ({'só revisadas' if args.so_revisadas else 'com rascunho'})\n")
    print(f"{'métrica':20}" + "".join(f"{c:>24}" for c in por_condicao))
    for metrica in METRICAS:
        celulas = []
        for c, linhas in por_condicao.items():
            item = intervalo([l[metrica] for l in linhas], sorteios)
            resultado["condicoes"].setdefault(c, {"metricas": {}})["metricas"][metrica] = item
            celulas.append(fmt(item))
        print(f"{metrica:20}" + "".join(f"{x:>24}" for x in celulas))
    print(f"\n{'recusa':20}" + "".join(f"{c:>24}" for c in por_condicao))
    for c, linhas in por_condicao.items():
        resultado["condicoes"][c]["recusa"] = intervalo_f1(linhas, sorteios)
    for chave in ("precisao", "cobertura", "f1"):
        print(f"{chave:20}" + "".join(f"{resultado['condicoes'][c]['recusa'][chave]:>24.3f}"
                                      for c in por_condicao))

    print("\nDesfecho das perguntas com resposta (contagem)")
    for c, linhas in por_condicao.items():
        contagem = Counter(l["desfecho"] for l in linhas if not l["sem_resposta"])
        resultado["condicoes"][c]["desfechos"] = dict(contagem)
        print(f"  {c:16} " + ", ".join(f"{k}={v}" for k, v in contagem.most_common()))

    print("\nPor tipo de pergunta: citou_relevante (com resposta) / recusa_correta (sem_resposta)")
    for c, linhas in por_condicao.items():
        grupos = defaultdict(list)
        for l in linhas:
            grupos[l["tipo"]].append(l)
        porte = {}
        for tipo in TIPOS:
            g = grupos.get(tipo, [])
            chave = "recusa_correta" if tipo == "sem_resposta" else "citou_relevante"
            vals = [l[chave] for l in g if l[chave] is not None]
            porte[tipo] = {"metrica": chave, "media": (sum(vals) / len(vals)) if vals else None,
                           "n": len(vals)}
        resultado["condicoes"][c]["por_tipo"] = porte
        print(f"  {c:16} " + "  ".join(
            f"{t}={v['media']:.2f} (n={v['n']})" if v["media"] is not None else f"{t}=—"
            for t, v in porte.items()))

    referencia = "hibrida-bge-m3"
    if referencia in por_condicao:
        print(f"\nDiferença em relação a {referencia} (bootstrap pareado; IC que inclui 0 = inconclusivo)")
        base = por_condicao[referencia]
        for metrica in ("citou_relevante", "precisao_citacao", "recusa_correta", "recusa_indevida"):
            for c, linhas in por_condicao.items():
                if c == referencia:
                    continue
                a = np.array([np.nan if l[metrica] is None else l[metrica] for l in linhas], float)
                b = np.array([np.nan if l[metrica] is None else l[metrica] for l in base], float)
                validos = ~np.isnan(a) & ~np.isnan(b)  # só perguntas em que ambas se aplicam
                if not validos.any():
                    continue
                d = np.where(validos, a - b, np.nan)
                medias = np.nanmean(d[sorteios], axis=1)
                ic = (np.nanpercentile(medias, 2.5), np.nanpercentile(medias, 97.5))
                resultado["condicoes"][c].setdefault("diferenca", {})[metrica] = {
                    "media": float(np.nanmean(d)), "ic95": [float(ic[0]), float(ic[1])],
                    "n": int(validos.sum())}
                print(f"  {metrica:18} {c:16} {np.nanmean(d):+.3f} [{ic[0]:+.2f}, {ic[1]:+.2f}] "
                      f"(n={int(validos.sum())})")

    sufixo = ("-revisadas" if args.so_revisadas else "") + (
        "" if set(args.condicoes) == set(CONDICOES) else "-parcial")
    destino = SAIDA / f"metricas-{args.modelo}{sufixo}.json"
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    por_pergunta = SAIDA / f"metricas-por-pergunta-{args.modelo}{sufixo}.jsonl"
    por_pergunta.write_text("".join(json.dumps({"condicao": c, **l}, ensure_ascii=False) + "\n"
                                    for c, linhas in por_condicao.items() for l in linhas),
                            encoding="utf-8")
    print(f"\nSalvo em {destino.name} e {por_pergunta.name}")


if __name__ == "__main__":
    main()
