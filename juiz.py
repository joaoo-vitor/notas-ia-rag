"""Avaliação das respostas por um juiz (LLM) e por uma pessoa, com as mesmas instruções.

As métricas automáticas (avaliar_geracao.py) medem citações e recusas. Isto mede o que só
lendo dá para julgar: se cada afirmação está apoiada nos trechos (fidelidade), se a resposta
está certa em relação aos trechos do gabarito (correção) e se ajuda a usuária (utilidade).
Critérios em avaliacao/geracao/juiz/instrucoes.md.

Etapas:
    preparar   monta os itens às cegas (sem dizer a condição, em ordem embaralhada), divide em
               lotes para o juiz e sorteia uma amostra para anotação humana
    juntar     lê as avaliações, calcula as médias por condição e a concordância juiz × pessoa

Uso (na raiz deste repositório):
    .venv/Scripts/python juiz.py preparar --modelo gemini-3.5-flash
    (o juiz grava avaliacao/geracao/juiz/avaliacoes/lote-NN.jsonl; a pessoa preenche humano.yaml)
    .venv/Scripts/python juiz.py juntar --modelo gemini-3.5-flash

Só entram respostas que não foram recusadas: a recusa já é medida automaticamente.
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

from geracao import (CONDICOES, SAIDA, caminho_contextos, caminho_respostas, carregar_gabarito,
                     carregar_trechos)

PASTA = SAIDA / "juiz"
CHAVE = PASTA / "chave.json"            # item -> condição e pergunta (o juiz não lê)
LOTES = PASTA / "lotes"
AVALIACOES = PASTA / "avaliacoes"
HUMANO = PASTA / "humano.yaml"
TAMANHO_LOTE = 25
AMOSTRA_POR_CONDICAO = 10
PONTOS_CORRECAO = {"correta": 1.0, "parcial": 0.5, "incorreta": 0.0}
N_BOOTSTRAP = 2000


def bloco(ids, trechos):
    return [{"n": i, "fonte": f"{trechos[t]['documento']}, {trechos[t]['secao']}",
             "texto": trechos[t]["texto"]} for i, t in enumerate(ids, start=1)]


# --- preparar ------------------------------------------------------------------------------

def preparar(modelo, semente):
    perguntas = {p["id"]: p for p in carregar_gabarito()[0]}
    trechos = carregar_trechos()
    oraculo = json.loads(caminho_contextos("oraculo").read_text(encoding="utf-8"))["perguntas"]
    itens = []
    for condicao in CONDICOES:
        arquivo = caminho_respostas(condicao, modelo)
        if not arquivo.exists():
            sys.exit(f"Faltam as respostas de {condicao}: {arquivo.name}")
        for r in map(json.loads, arquivo.read_text(encoding="utf-8").splitlines()):
            if r["sem_base"]:
                continue
            itens.append({
                "condicao": condicao, "pergunta_id": r["id"], "tipo": r["tipo"],
                "pergunta": perguntas[r["id"]]["pergunta"], "resposta": r["resposta"],
                "trechos_fornecidos": bloco(r["trechos"], trechos),
                "referencia": bloco(oraculo[r["id"]]["trechos"], trechos),
            })
    random.Random(semente).shuffle(itens)
    chave = {}
    for n, item in enumerate(itens, start=1):
        item["item"] = f"j{n:04d}"
        chave[item["item"]] = {"condicao": item["condicao"], "pergunta": item["pergunta_id"],
                               "tipo": item["tipo"]}
    PASTA.mkdir(parents=True, exist_ok=True)
    LOTES.mkdir(exist_ok=True)
    AVALIACOES.mkdir(exist_ok=True)
    CHAVE.write_text(json.dumps(chave, ensure_ascii=False, indent=1), encoding="utf-8")
    campos = ("item", "pergunta", "resposta", "trechos_fornecidos", "referencia")
    cegos = [{c: i[c] for c in campos} for i in itens]
    for arquivo in LOTES.glob("lote-*.json"):
        arquivo.unlink()
    for k in range(0, len(cegos), TAMANHO_LOTE):
        destino = LOTES / f"lote-{k // TAMANHO_LOTE + 1:02d}.json"
        destino.write_text(json.dumps(cegos[k:k + TAMANHO_LOTE], ensure_ascii=False, indent=1),
                           encoding="utf-8")

    # amostra humana: o mesmo número de itens por condição, sorteados
    por_condicao = defaultdict(list)
    for item in itens:
        por_condicao[item["condicao"]].append(item)
    sorteador = random.Random(semente + 1)
    amostra = []
    for condicao in CONDICOES:
        amostra += sorteador.sample(por_condicao[condicao],
                                    min(AMOSTRA_POR_CONDICAO, len(por_condicao[condicao])))
    sorteador.shuffle(amostra)
    if HUMANO.exists():
        print(f"{HUMANO.name} já existe; não sobrescrevi (apague para sortear de novo)")
    else:
        cabecalho = ("# Anotação humana: siga avaliacao/geracao/juiz/instrucoes.md e preencha, em cada\n"
                     "# item, fiel (sim: todas as afirmações apoiadas | nao), correcao e utilidade.\n"
                     "# Não abra chave.json nem as avaliações do juiz antes de terminar.\n")
        dados = [{**{c: i[c] for c in campos}, "fiel": None, "correcao": None, "utilidade": None,
                  "comentario": ""} for i in amostra]
        HUMANO.write_text(cabecalho + yaml.safe_dump(dados, allow_unicode=True, sort_keys=False,
                                                     width=100), encoding="utf-8")
    contagem = Counter(i["condicao"] for i in itens)
    print(f"{len(itens)} itens em {len(list(LOTES.glob('lote-*.json')))} lotes de até {TAMANHO_LOTE}: "
          + ", ".join(f"{c}={contagem[c]}" for c in CONDICOES))
    print(f"Amostra humana: {len(amostra)} itens em {HUMANO.name}")


# --- juntar --------------------------------------------------------------------------------

def ler_avaliacoes():
    avaliacoes = {}
    for arquivo in sorted(AVALIACOES.glob("lote-*.jsonl")):
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                a = json.loads(linha)
                avaliacoes[a["item"]] = a
    return avaliacoes


def resumo_item(a):
    rotulos = [x["rotulo"] for x in a.get("afirmacoes", [])]
    return {
        "fidelidade": (rotulos.count("apoiada") / len(rotulos)) if rotulos else None,
        "fiel": float(all(r == "apoiada" for r in rotulos)) if rotulos else None,
        "contradiz": float("contradiz" in rotulos) if rotulos else None,
        "afirmacoes": len(rotulos),
        "correcao": PONTOS_CORRECAO.get(a["correcao"]),
        "correta": None if a["correcao"] == "sem_referencia" else float(a["correcao"] == "correta"),
        "utilidade": float(a["utilidade"]),
    }


def kappa(a, b, categorias, pesos=None):
    """Kappa de Cohen; com pesos='quadraticos' para escala ordinal."""
    indice = {c: i for i, c in enumerate(categorias)}
    k = len(categorias)
    obs = np.zeros((k, k))
    for x, y in zip(a, b):
        obs[indice[x], indice[y]] += 1
    obs /= obs.sum()
    esperado = np.outer(obs.sum(axis=1), obs.sum(axis=0))
    if pesos == "quadraticos":
        w = np.array([[(i - j) ** 2 for j in range(k)] for i in range(k)]) / (k - 1) ** 2
    else:
        w = 1 - np.eye(k)
    denominador = (w * esperado).sum()
    return float(1 - (w * obs).sum() / denominador) if denominador else 1.0


def concordancia(avaliacoes):
    if not HUMANO.exists():
        return None
    humanos = [h for h in yaml.safe_load(HUMANO.read_text(encoding="utf-8"))
               if h.get("correcao") and h.get("utilidade") and h.get("fiel") is not None]
    pares = [(h, avaliacoes[h["item"]]) for h in humanos if h["item"] in avaliacoes]
    if not pares:
        return None
    sim = lambda v: "sim" if v in (True, "sim") else "nao"  # noqa: E731
    fiel_h = [sim(h["fiel"]) for h, _ in pares]
    fiel_j = ["sim" if resumo_item(j)["fiel"] in (1.0, None) else "nao" for _, j in pares]
    corr_h = [h["correcao"] for h, _ in pares]
    corr_j = [j["correcao"] for _, j in pares]
    util_h = [int(h["utilidade"]) for h, _ in pares]
    util_j = [int(j["utilidade"]) for _, j in pares]
    return {
        "itens": len(pares),
        "fiel": {"acordo": float(np.mean([a == b for a, b in zip(fiel_h, fiel_j)])),
                 "kappa": kappa(fiel_h, fiel_j, ["sim", "nao"])},
        "correcao": {"acordo": float(np.mean([a == b for a, b in zip(corr_h, corr_j)])),
                     "kappa": kappa(corr_h, corr_j, ["correta", "parcial", "incorreta", "sem_referencia"])},
        "utilidade": {"acordo_exato": float(np.mean([a == b for a, b in zip(util_h, util_j)])),
                      "acordo_1": float(np.mean([abs(a - b) <= 1 for a, b in zip(util_h, util_j)])),
                      "kappa_quadratico": kappa(util_h, util_j, [1, 2, 3, 4, 5], "quadraticos")},
    }


def juntar(modelo, semente):
    chave = json.loads(CHAVE.read_text(encoding="utf-8"))
    avaliacoes = ler_avaliacoes()
    faltando = sorted(set(chave) - set(avaliacoes))
    print(f"{len(avaliacoes)} de {len(chave)} itens avaliados pelo juiz"
          + (f"; faltam {len(faltando)} (ex.: {', '.join(faltando[:5])})" if faltando else ""))
    linhas = defaultdict(dict)  # condição -> pergunta -> resumo
    for item, a in avaliacoes.items():
        if item in chave:
            linhas[chave[item]["condicao"]][chave[item]["pergunta"]] = resumo_item(a)

    metricas = ("fidelidade", "fiel", "contradiz", "correcao", "correta", "utilidade")
    rng = np.random.default_rng(semente)
    resultado = {"modelo": modelo, "itens_avaliados": len(avaliacoes), "condicoes": {}}
    print(f"\n{'métrica':12}" + "".join(f"{c:>24}" for c in CONDICOES if c in linhas))
    for metrica in metricas:
        celulas = []
        for c in CONDICOES:
            if c not in linhas:
                continue
            v = np.array([x[metrica] for x in linhas[c].values() if x[metrica] is not None], float)
            if not len(v):
                celulas.append("—")
                continue
            medias = v[rng.integers(0, len(v), size=(N_BOOTSTRAP, len(v)))].mean(axis=1)
            item = {"media": float(v.mean()), "ic95": [float(np.percentile(medias, 2.5)),
                                                       float(np.percentile(medias, 97.5))], "n": len(v)}
            resultado["condicoes"].setdefault(c, {})[metrica] = item
            celulas.append(f"{item['media']:.3f} [{item['ic95'][0]:.2f}–{item['ic95'][1]:.2f}]")
        print(f"{metrica:12}" + "".join(f"{x:>24}" for x in celulas))

    print("\nDiferença pareada em relação a hibrida-bge-m3 (mesmas perguntas, ambas respondidas)")
    base = linhas.get("hibrida-bge-m3", {})
    for metrica in ("fidelidade", "correcao", "utilidade"):
        for c in CONDICOES:
            if c == "hibrida-bge-m3" or c not in linhas:
                continue
            comuns = [q for q in linhas[c] if q in base
                      and linhas[c][q][metrica] is not None and base[q][metrica] is not None]
            if not comuns:
                continue
            d = np.array([linhas[c][q][metrica] - base[q][metrica] for q in comuns])
            medias = d[rng.integers(0, len(d), size=(N_BOOTSTRAP, len(d)))].mean(axis=1)
            ic = [float(np.percentile(medias, 2.5)), float(np.percentile(medias, 97.5))]
            resultado["condicoes"][c].setdefault("diferenca", {})[metrica] = {
                "media": float(d.mean()), "ic95": ic, "n": len(d)}
            print(f"  {metrica:11} {c:16} {d.mean():+.3f} [{ic[0]:+.2f}, {ic[1]:+.2f}] (n={len(d)})")

    acordo = concordancia(avaliacoes)
    resultado["concordancia_humana"] = acordo
    if acordo:
        print(f"\nConcordância juiz × pessoa ({acordo['itens']} itens):")
        print(f"  fiel       acordo {acordo['fiel']['acordo']:.2f}, kappa {acordo['fiel']['kappa']:.2f}")
        print(f"  correção   acordo {acordo['correcao']['acordo']:.2f}, kappa {acordo['correcao']['kappa']:.2f}")
        u = acordo["utilidade"]
        print(f"  utilidade  exato {u['acordo_exato']:.2f}, ±1 {u['acordo_1']:.2f}, "
              f"kappa quadrático {u['kappa_quadratico']:.2f}")
    else:
        print("\nSem anotação humana preenchida ainda (humano.yaml).")
    destino = SAIDA / f"juiz-{modelo}.json"
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nSalvo em {destino.name}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("etapa", choices=("preparar", "juntar"))
    parser.add_argument("--modelo", required=True, help="modelo que gerou as respostas")
    parser.add_argument("--semente", type=int, default=13)
    args = parser.parse_args()
    if args.etapa == "preparar":
        preparar(args.modelo, args.semente)
    else:
        juntar(args.modelo, args.semente)


if __name__ == "__main__":
    main()
