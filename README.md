# notas-ia-rag

RAG jurídico do [Notas.ia](https://github.com/aline-fraga/notas-ia): recupera trechos de
leis oficiais para responder dúvidas com citação. No repositório principal, este
repositório fica na pasta `rag/` como submodule.

Desenho da solução em
[`arquitetura-chat-codex-eduardo-f/resposta-rag.md`](https://github.com/aline-fraga/notas-ia/blob/main/arquitetura-chat-codex-eduardo-f/resposta-rag.md)
(SQLite FTS5 + FAISS + RRF).

## Como rodar

Na raiz deste repositório (ou em `notas-ia/rag/`). No Windows; em Linux/Mac troque
`Scripts` por `bin`:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python ingestao.py              # gera o corpus a partir de fontes.yaml
.venv/Scripts/python busca_lexical.py --indexar
.venv/Scripts/python busca_densa.py --indexar --modelo e5-small   # baixa o modelo na 1ª vez
.venv/Scripts/python busca_densa.py "meu ex fica me seguindo" --hibrida
.venv/Scripts/python avaliar.py --recuperador hibrida-e5-small --incluir-rascunho
```

O índice denso precisa ser refeito sempre que o corpus mudar (o script avisa).

## Arquivos

| Arquivo | O que é |
|---|---|
| `fontes.yaml` | Lista das fontes oficiais. Só entra no corpus o que estiver aqui |
| `ingestao.py` | Baixa as páginas do Planalto e divide em trechos por artigo |
| `dados/brutos/` | Páginas originais, como vieram do site (para reproduzir o corpus) |
| `corpus/trechos.jsonl` | Um trecho por linha, com texto e metadados |
| `corpus/manifesto.json` | Versão do corpus (hash), data e contagem por fonte |
| `busca_lexical.py` | Busca BM25 com SQLite FTS5 (linha de base) |
| `busca_densa.py` | Busca densa (embeddings + FAISS) e híbrida (BM25 + densa por RRF) |
| `avaliacao/gabarito.yaml` | Perguntas leigas com os artigos que as respondem |
| `avaliar.py` | Mede acerto@k, recall@k, MRR e nDCG@10 contra o gabarito |
| `avaliacao/resultados/` | Resultado de cada avaliação, por recuperador e versão do corpus |

## Gabarito

O gabarito é a referência para comparar as buscas, então precisa ser feito por pessoas:

1. Escrever a pergunta como uma usuária escreveria e indicar o `tipo`.
2. Apontar os artigos que respondem (`relevantes`) e os que ajudam (`parciais`).
3. Duas pessoas conferem, sem ver a resposta uma da outra; divergências são discutidas.
4. Só então o `status` vira `revisado`. Por padrão, o `avaliar.py` usa só os revisados.

As 32 perguntas atuais são um **rascunho** escrito com auxílio de IA. Meta: 150 a 300.

## Resultados (provisórios)

Corpus `428510e4d5f5`, gabarito com 29 perguntas em **rascunho** (ainda não revisadas).
Com tão poucas perguntas, uma pergunta vale ~3,4 pontos: diferenças pequenas não são
conclusivas.

| Recuperador | acerto@1 | acerto@3 | acerto@5 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| BM25 (`lexical`) | 0,45 | 0,55 | 0,72 | 0,55 | 0,59 |
| Densa e5-small | 0,48 | 0,69 | 0,79 | 0,61 | 0,66 |
| Híbrida e5-small | 0,52 | 0,79 | 0,83 | 0,64 | 0,67 |
| Densa e5-base | 0,45 | 0,62 | 0,90 | 0,59 | 0,63 |
| Híbrida e5-base | 0,41 | 0,76 | 0,86 | 0,60 | 0,64 |
| **Densa BGE-M3** | **0,66** | **0,79** | **0,86** | **0,75** | **0,76** |
| Híbrida BGE-M3 | 0,52 | 0,76 | 0,83 | 0,66 | 0,70 |

- Toda busca densa supera a BM25, principalmente em perguntas coloquiais.
- A BGE-M3 sozinha foi a melhor; somar a BM25 a ela piorou o ranking.
- **Nenhum recuperador separa as perguntas sem resposta**: o escore do 1º resultado
  delas fica na mesma faixa das perguntas com resposta. A recusa vai precisar de outro
  sinal (reranqueador ou verificação pela LLM), não de um limiar no escore da busca.

## Decisões da ingestão

- **Um trecho por artigo**, com caput, incisos, parágrafos e penas. Artigos com mais de
  2.500 caracteres são divididos em partes que repetem o caput.
- **Texto revogado é descartado.** O Planalto mantém a redação antiga riscada
  (`<strike>` ou `line-through`); ela nunca entra no corpus.
- **Anotações de alteração** ("Incluído pela Lei nº ...") saem do texto e vão para o
  campo `alterado_por`.
- **Leis que só alteram outras leis** não entram: o texto já está na versão compilada.
- **Rubricas** do Código Penal ("Calúnia", "Lesão corporal") ficam no início do trecho,
  porque ajudam a busca.

## Limitações conhecidas

- O corpus ainda não foi revisado por alguém da área jurídica.
- A busca lexical não liga linguagem coloquial à linguagem da lei ("fica me seguindo"
  não encontra "perseguição"). É o que a busca densa deve resolver.
- Ainda não há notas em linguagem simples nem cartilhas (só texto de lei).
