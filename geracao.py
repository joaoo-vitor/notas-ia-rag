"""Geração de respostas com o Gemini a partir dos trechos recuperados (o "G" do RAG).

Duas etapas, separadas para que a geração não precise carregar o modelo de embeddings:

1. contextos: para cada pergunta do gabarito, guarda os trechos que o modelo vai receber.
   Condições:
     sem-busca        nenhum trecho (linha de base: o que o modelo responde sozinho)
     lexical          os k primeiros da BM25
     hibrida-bge-m3   os k primeiros da híbrida (BM25 + BGE-M3 por RRF)
     oraculo          os trechos relevantes do gabarito (teto: erro aqui é da geração)
2. responder: manda pergunta + trechos ao Gemini e guarda resposta, citações e recusa.
   Retoma de onde parou: perguntas já respondidas com o mesmo pedido são puladas.

Uso (na raiz deste repositório), com GEMINI_API_KEY no ambiente para a etapa 2:
    .venv/Scripts/python geracao.py contextos --condicao lexical
    .venv/Scripts/python geracao.py contextos --condicao hibrida-bge-m3
    .venv/Scripts/python geracao.py contextos --condicao oraculo
    .venv/Scripts/python geracao.py responder --condicao lexical --limite 10
    .venv/Scripts/python geracao.py responder --condicao lexical

Saídas em avaliacao/geracao/: contextos-<condição>.json e respostas-<condição>-<modelo>.jsonl.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import yaml

import gemini
from avaliar import GABARITO, mapa_trechos, rotulos_por_trecho

RAIZ = Path(__file__).resolve().parent
TRECHOS = RAIZ / "corpus" / "trechos.jsonl"
SAIDA = RAIZ / "avaliacao" / "geracao"
CONDICOES = ("sem-busca", "lexical", "hibrida-bge-m3", "oraculo")
K_PADRAO = 5

# --- instruções ----------------------------------------------------------------------------

SISTEMA_COM_TRECHOS = """Você orienta mulheres em situação de violência doméstica e familiar \
no Brasil sobre os direitos delas, com base em trechos de leis brasileiras.

Você recebe a pergunta de uma usuária e trechos de lei numerados. Regras:
1. Use só o que está nos trechos. Não acrescente direitos, prazos, penas, órgãos ou \
procedimentos que não estejam neles, mesmo que você os conheça.
2. Toda afirmação sobre a lei deve indicar de qual trecho vem, com o número entre \
colchetes logo depois da frase, por exemplo: "Isso é violência patrimonial [2]."
3. Se os trechos não respondem à pergunta, diga com clareza que não encontrou a resposta \
nas leis consultadas, não tente responder e marque sem_base como true. Se respondem só \
em parte, responda a parte que eles cobrem e diga o que ficou sem resposta.
4. Escreva em português simples e acolhedor, sem juridiquês, em no máximo 150 palavras.
5. Não julgue a usuária, não decida o caso dela e não prometa resultado.

Devolva: resposta (o texto para a usuária), citacoes (os números dos trechos que você \
citou na resposta) e sem_base (true se os trechos não respondem à pergunta)."""

SISTEMA_SEM_TRECHOS = """Você orienta mulheres em situação de violência doméstica e familiar \
no Brasil sobre os direitos delas, com base na legislação brasileira.

Regras:
1. Responda com o que você sabe da legislação brasileira. Quando afirmar algo sobre a lei, \
diga de qual lei e artigo vem, se souber.
2. Se não souber responder ou a pergunta não for sobre a legislação, diga isso com \
clareza, não tente responder e marque sem_base como true.
3. Escreva em português simples e acolhedor, sem juridiquês, em no máximo 150 palavras.
4. Não julgue a usuária, não decida o caso dela e não prometa resultado.

Devolva: resposta (o texto para a usuária), artigos_citados (cada lei e artigo que você \
mencionou, por exemplo "Lei 11.340/2006, art. 7") e sem_base (true se não soube responder)."""

ESQUEMA_COM_TRECHOS = {
    "type": "OBJECT",
    "properties": {
        "resposta": {"type": "STRING"},
        "citacoes": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "sem_base": {"type": "BOOLEAN"},
    },
    "required": ["resposta", "citacoes", "sem_base"],
    "propertyOrdering": ["resposta", "citacoes", "sem_base"],
}

ESQUEMA_SEM_TRECHOS = {
    "type": "OBJECT",
    "properties": {
        "resposta": {"type": "STRING"},
        "artigos_citados": {"type": "ARRAY", "items": {"type": "STRING"}},
        "sem_base": {"type": "BOOLEAN"},
    },
    "required": ["resposta", "artigos_citados", "sem_base"],
    "propertyOrdering": ["resposta", "artigos_citados", "sem_base"],
}

MARCA_CITACAO = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")  # [2] ou [1, 4]


# --- dados ---------------------------------------------------------------------------------

def carregar_gabarito():
    texto = GABARITO.read_text(encoding="utf-8")
    versao = hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]
    return yaml.safe_load(texto)["perguntas"], versao


def carregar_trechos():
    return {t["id"]: t for t in map(json.loads, TRECHOS.read_text(encoding="utf-8").splitlines())}


def versao_do_corpus():
    return json.loads((RAIZ / "corpus" / "manifesto.json").read_text(encoding="utf-8"))["versao"]


def caminho_contextos(condicao):
    return SAIDA / f"contextos-{condicao}.json"


def caminho_respostas(condicao, modelo):
    return SAIDA / f"respostas-{condicao}-{modelo}.jsonl"


# --- etapa 1: contextos --------------------------------------------------------------------

def trechos_do_oraculo(pergunta, trechos_por_artigo):
    """Trechos relevantes do gabarito, na ordem do corpus. Artigo dividido sem a parte exata
    marcada entra inteiro (todas as partes): é o que o gabarito permite afirmar."""
    relevantes, _parciais, pendentes = rotulos_por_trecho(pergunta, trechos_por_artigo)
    escolhidos = set(relevantes)
    for artigo in pendentes:
        if artigo in (pergunta.get("relevantes") or []):
            escolhidos.update(trechos_por_artigo[artigo])
    ordem = [t for partes in trechos_por_artigo.values() for t in partes]
    return [t for t in ordem if t in escolhidos]


def gerar_contextos(condicao, k):
    perguntas, versao_gabarito = carregar_gabarito()
    trechos_por_artigo = mapa_trechos(TRECHOS)
    if condicao in ("lexical", "hibrida-bge-m3"):
        from avaliar import recuperadores
        busca = recuperadores()[condicao]()
    itens = {}
    for p in perguntas:
        if condicao == "sem-busca":
            ids, scores = [], []
        elif condicao == "oraculo":
            ids = trechos_do_oraculo(p, trechos_por_artigo)
            scores = [None] * len(ids)
        else:
            resultados = busca.search(p["pergunta"], k)
            ids = [r["id"] for r in resultados]
            scores = [round(float(r["score"]), 6) for r in resultados]
        itens[p["id"]] = {"trechos": ids, "scores": scores}
    SAIDA.mkdir(parents=True, exist_ok=True)
    destino = caminho_contextos(condicao)
    destino.write_text(json.dumps({
        "condicao": condicao,
        "k": None if condicao in ("sem-busca", "oraculo") else k,
        "corpus": versao_do_corpus(),
        "gabarito": versao_gabarito,
        "data": date.today().isoformat(),
        "perguntas": itens,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    tamanhos = [len(v["trechos"]) for v in itens.values()]
    print(f"{destino.name}: {len(itens)} perguntas, {sum(tamanhos)} trechos "
          f"(média {sum(tamanhos) / len(tamanhos):.1f} por pergunta)")


# --- etapa 2: respostas --------------------------------------------------------------------

def montar_pedido(pergunta, ids, trechos):
    """ids None = condição sem-busca; lista vazia = busca que não trouxe nada."""
    if ids is None:
        texto = f"Pergunta da usuária: {pergunta}"
        return gemini.corpo_do_pedido(texto, esquema=ESQUEMA_SEM_TRECHOS, sistema=SISTEMA_SEM_TRECHOS)
    blocos = [f"[{i}] {trechos[t]['documento']}, {trechos[t]['secao']}\n{trechos[t]['texto']}"
              for i, t in enumerate(ids, start=1)]
    texto = (f"Pergunta da usuária: {pergunta}\n\nTrechos de lei:\n\n"
             + ("\n\n".join(blocos) if blocos else "(nenhum trecho encontrado)"))
    return gemini.corpo_do_pedido(texto, esquema=ESQUEMA_COM_TRECHOS, sistema=SISTEMA_COM_TRECHOS)


def hash_do_pedido(corpo, modelo):
    bruto = json.dumps({"modelo": modelo, "corpo": corpo}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


def registro(p, condicao, ids, corpo, modelo, saida):
    dados = saida["json"]
    texto = dados["resposta"].strip()
    item = {
        "id": p["id"], "tipo": p["tipo"], "status": p.get("status"), "condicao": condicao,
        "pergunta": p["pergunta"], "trechos": ids or [],
        "resposta": texto, "sem_base": bool(dados["sem_base"]),
        "modelo": saida["modelo"], "fim": saida["fim"], "uso": saida["uso"],
        "pedido": hash_do_pedido(corpo, modelo), "data": date.today().isoformat(),
    }
    if ids is None:
        item["artigos_citados"] = dados.get("artigos_citados", [])
    else:
        # o que conta é a marca [n] no texto; a lista devolvida serve para conferência
        no_texto = [int(n) for grupo in MARCA_CITACAO.findall(texto) for n in re.findall(r"\d+", grupo)]
        item["citacoes_declaradas"] = dados.get("citacoes", [])
        item["citacoes_texto"] = list(dict.fromkeys(no_texto))
        item["citacoes_invalidas"] = sorted({n for n in no_texto if not 1 <= n <= len(ids)})
        item["citacoes"] = [ids[n - 1] for n in item["citacoes_texto"] if 1 <= n <= len(ids)]
    return item


def responder(condicao, modelo, limite, apenas, pausa):
    perguntas, versao_gabarito = carregar_gabarito()
    trechos = carregar_trechos()
    contextos = None
    if condicao != "sem-busca":
        arquivo = caminho_contextos(condicao)
        if not arquivo.exists():
            sys.exit(f"Falta {arquivo.name}: rode antes `geracao.py contextos --condicao {condicao}`")
        contextos = json.loads(arquivo.read_text(encoding="utf-8"))
        if contextos["corpus"] != versao_do_corpus():
            sys.exit(f"{arquivo.name} é de outro corpus ({contextos['corpus']}); gere de novo")
        if contextos["gabarito"] != versao_gabarito:
            print(f"AVISO: {arquivo.name} foi gerado com outra versão do gabarito")

    destino = caminho_respostas(condicao, modelo)
    feitos = {}
    if destino.exists():
        for linha in destino.read_text(encoding="utf-8").splitlines():
            r = json.loads(linha)
            feitos[r["id"]] = r["pedido"]

    pendentes = []
    for p in perguntas:
        if apenas and p["id"] not in apenas:
            continue
        ids = None if contextos is None else contextos["perguntas"][p["id"]]["trechos"]
        corpo = montar_pedido(p["pergunta"], ids, trechos)
        if feitos.get(p["id"]) == hash_do_pedido(corpo, modelo):
            continue
        pendentes.append((p, ids, corpo))
    if limite:
        pendentes = pendentes[:limite]
    print(f"{len(pendentes)} perguntas para responder ({condicao}, {modelo}); "
          f"{len(feitos)} já no arquivo")

    SAIDA.mkdir(parents=True, exist_ok=True)
    chave = gemini.chave_do_ambiente()
    for n, (p, ids, corpo) in enumerate(pendentes, start=1):
        try:
            saida = gemini.chamar(corpo, modelo=modelo, chave=chave)
        except gemini.ErroGemini as erro:
            sys.exit(f"{p['id']}: {erro}\nO que já foi respondido está salvo; rode de novo para continuar.")
        item = registro(p, condicao, ids, corpo, modelo, saida)
        # reescreve sem a versão antiga da mesma pergunta (pedido mudou) e acrescenta a nova
        linhas = []
        if destino.exists():
            linhas = [l for l in destino.read_text(encoding="utf-8").splitlines()
                      if json.loads(l)["id"] != p["id"]]
        linhas.append(json.dumps(item, ensure_ascii=False))
        destino.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        marca = "recusa" if item["sem_base"] else f"{len(item.get('citacoes', item.get('artigos_citados', [])))} citações"
        print(f"  {time.strftime('%H:%M:%S')} {n}/{len(pendentes)} {p['id']} ({p['tipo']}): {marca}")
        if n < len(pendentes):
            time.sleep(pausa)
    print(f"Pronto: {destino}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="etapa", required=True)
    c = sub.add_parser("contextos", help="guarda os trechos de cada pergunta para uma condição")
    c.add_argument("--condicao", required=True, choices=CONDICOES)
    c.add_argument("--k", type=int, default=K_PADRAO, help="trechos por pergunta (busca)")
    r = sub.add_parser("responder", help="gera as respostas com o Gemini")
    r.add_argument("--condicao", required=True, choices=CONDICOES)
    r.add_argument("--modelo", default=gemini.MODELO_PADRAO)
    r.add_argument("--limite", type=int, help="responde só as N primeiras pendentes")
    r.add_argument("--apenas", nargs="*", help="ids de perguntas específicas")
    r.add_argument("--pausa", type=float, default=13, help="segundos entre chamadas (free tier: 5/min)")
    args = parser.parse_args()
    if args.etapa == "contextos":
        gerar_contextos(args.condicao, args.k)
    else:
        responder(args.condicao, args.modelo, args.limite, args.apenas, args.pausa)


if __name__ == "__main__":
    main()
