"""Gera palavras-chave e expressões coloquiais para os trechos do corpus com o Gemini.

O Gemini recebe SÓ o texto oficial de cada trecho (documento, seção e texto). Nunca recebe
o gabarito: termos tirados das perguntas do gabarito fariam a avaliação subir sem medir
nada. O resultado vai para enriquecimento.yaml, que deve ser revisado por uma pessoa
(só para TIRAR termos errados, nunca para acrescentar termos vindos do gabarito).

Uso (na raiz deste repositório), com a chave no ambiente:
    set GEMINI_API_KEY=...                      (PowerShell: $env:GEMINI_API_KEY="...")
    .venv/Scripts/python gerar_palavras_chave.py                 # só trechos ainda sem enriquecimento
    .venv/Scripts/python gerar_palavras_chave.py --sobrescrever   # gera de novo para todos
    .venv/Scripts/python gerar_palavras_chave.py --apenas lei-11340-2006:art21

Depois: rodar ingestao.py e reindexar (busca_lexical.py --indexar e busca_densa.py --indexar).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent
TRECHOS = RAIZ / "corpus" / "trechos.jsonl"
SAIDA = RAIZ / "enriquecimento.yaml"
API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
CATEGORIAS = ["fisica", "psicologica", "sexual", "patrimonial", "moral", "vicaria"]

INSTRUCOES = """Você ajuda a montar a busca de um assistente para mulheres em situação de \
violência doméstica no Brasil. Ela escreve como fala, sem termos jurídicos, muitas vezes \
com gíria, erro de digitação e sem acento. A busca precisa achar o trecho de lei certo \
mesmo assim.

Para cada trecho de lei abaixo, escreva:
- palavras_chave: 5 a 15 palavras ou termos curtos que uma mulher leiga usaria para falar \
das situações que o trecho trata (sinônimos populares, gírias, termos do dia a dia). \
Não repita termos que já estão no texto da lei.
- expressoes: 5 a 10 frases curtas, em primeira pessoa ou como pergunta, do jeito que ela \
escreveria num chat para descrever a situação ou perguntar sobre ela.
- categorias: quais destes tipos de violência o trecho trata diretamente: fisica, \
psicologica, sexual, patrimonial, moral, vicaria. Lista vazia se o trecho for geral \
(procedimento, órgão público, medida que vale para qualquer tipo).

Regras:
- Fique no que o trecho realmente diz. Não acrescente direitos, prazos, serviços ou \
consequências que não estejam no texto.
- Não use nomes de pessoas, lugares ou números de telefone.
- Português do Brasil.

Responda só com o JSON pedido, com um item por trecho, usando o mesmo id."""

ESQUEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "palavras_chave": {"type": "ARRAY", "items": {"type": "STRING"}},
            "expressoes": {"type": "ARRAY", "items": {"type": "STRING"}},
            "categorias": {"type": "ARRAY", "items": {"type": "STRING", "enum": CATEGORIAS}},
        },
        "required": ["id", "palavras_chave", "expressoes", "categorias"],
    },
}


def chamar_gemini(chave, modelo, trechos, tentativas=5):
    texto = INSTRUCOES + "\n\n" + "\n\n".join(
        f"### id: {t['id']}\n{t['documento']}, {t['secao']}\n{t['texto']}" for t in trechos
    )
    corpo = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": texto}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json",
                             "responseSchema": ESQUEMA},
    }).encode("utf-8")
    pedido = urllib.request.Request(API.format(modelo=modelo), data=corpo, method="POST", headers={
        "Content-Type": "application/json", "x-goog-api-key": chave})
    for tentativa in range(tentativas):
        try:
            with urllib.request.urlopen(pedido, timeout=300) as resposta:
                dados = json.loads(resposta.read().decode("utf-8"))
            return json.loads(dados["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as erro:
            if erro.code not in (429, 500, 503) or tentativa == tentativas - 1:
                sys.exit(f"Erro do Gemini (HTTP {erro.code}): {erro.read().decode('utf-8', 'replace')[:500]}")
            motivo = f"HTTP {erro.code}"
        except (TimeoutError, urllib.error.URLError, ConnectionError) as erro:
            # servidor sobrecarregado costuma aparecer como demora sem resposta
            if tentativa == tentativas - 1:
                sys.exit(f"O Gemini não respondeu: {erro}")
            motivo = f"sem resposta ({type(erro).__name__})"
        espera = 30 * (tentativa + 1)  # limite ou sobrecarga do free tier: espera e tenta de novo
        print(f"  {motivo}; tentando de novo em {espera} s")
        time.sleep(espera)
    sys.exit("O Gemini não respondeu depois de várias tentativas.")


def limpar(lista):
    vistos, saida = set(), []
    for item in lista:
        item = " ".join(str(item).split())
        if item and item.lower() not in vistos:
            vistos.add(item.lower())
            saida.append(item)
    return saida


def salvar(dados):
    cabecalho = SAIDA.read_text(encoding="utf-8").split("\ntrechos:")[0] if SAIDA.exists() else ""
    corpo = yaml.safe_dump({"trechos": dados}, allow_unicode=True, sort_keys=False, width=100)
    SAIDA.write_bytes(((cabecalho + "\n" if cabecalho else "") + corpo).encode("utf-8"))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modelo", default="gemini-3.8-flash",
                        help="modelo do Gemini (padrão: gemini-3.8-flash)")
    parser.add_argument("--lote", type=int, default=5, help="trechos por chamada")
    parser.add_argument("--pausa", type=float, default=7, help="segundos entre chamadas (free tier)")
    parser.add_argument("--sobrescrever", action="store_true")
    parser.add_argument("--apenas", nargs="*", help="ids de trechos específicos")
    parser.add_argument("--listar-modelos", action="store_true",
                        help="mostra os modelos disponíveis para a sua chave e sai")
    args = parser.parse_args()

    chave = os.environ.get("GEMINI_API_KEY")
    if not chave:
        sys.exit("Defina a variável de ambiente GEMINI_API_KEY com a sua chave do Gemini.")

    if args.listar_modelos:
        pedido = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                        headers={"x-goog-api-key": chave})
        with urllib.request.urlopen(pedido, timeout=60) as resposta:
            modelos = json.loads(resposta.read().decode("utf-8")).get("models", [])
        for m in modelos:
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(m["name"].removeprefix("models/"))
        return

    trechos = [json.loads(l) for l in TRECHOS.read_text(encoding="utf-8").splitlines()]
    existentes = (yaml.safe_load(SAIDA.read_text(encoding="utf-8")) or {}).get("trechos") or {} \
        if SAIDA.exists() else {}
    pendentes = [t for t in trechos
                 if (not args.apenas or t["id"] in args.apenas)
                 and (args.sobrescrever or args.apenas or t["id"] not in existentes)]
    print(f"{len(pendentes)} trechos para gerar com {args.modelo}, {args.lote} por chamada")

    dados = dict(existentes)
    for i in range(0, len(pendentes), args.lote):
        lote = pendentes[i:i + args.lote]
        respostas = {r["id"]: r for r in chamar_gemini(chave, args.modelo, lote)}
        for t in lote:
            r = respostas.get(t["id"])
            if not r:
                print(f"  AVISO: sem resposta para {t['id']}; rode de novo para completar")
                continue
            dados[t["id"]] = {
                "palavras_chave": limpar(r["palavras_chave"]),
                "expressoes": limpar(r["expressoes"]),
                "categorias": [c for c in CATEGORIAS if c in r["categorias"]],
            }
        salvar({t["id"]: dados[t["id"]] for t in trechos if t["id"] in dados})  # ordem do corpus
        print(f"  {min(i + args.lote, len(pendentes))}/{len(pendentes)}")
        if i + args.lote < len(pendentes):
            time.sleep(args.pausa)
    print(f"Pronto: {SAIDA}. Revise, rode ingestao.py e reindexe.")


if __name__ == "__main__":
    main()
