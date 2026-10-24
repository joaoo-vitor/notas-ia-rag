# RAG jurídico (Trilha 2)

Recupera trechos de leis oficiais para responder dúvidas com citação. Desenho em
`arquitetura-chat-codex-eduardo-f/resposta-rag.md` (SQLite FTS5 + FAISS + RRF).

## Como rodar

Na raiz do repositório (Windows; em Linux/Mac troque `Scripts` por `bin`):

```bash
python -m venv rag/.venv
rag/.venv/Scripts/python -m pip install -r rag/requirements.txt
rag/.venv/Scripts/python rag/ingestao.py              # gera o corpus a partir de rag/fontes.yaml
rag/.venv/Scripts/python rag/busca_lexical.py --indexar
rag/.venv/Scripts/python rag/busca_lexical.py "o que acontece se ele descumprir a medida protetiva"
```

## Arquivos

| Arquivo | O que é |
|---|---|
| `fontes.yaml` | Lista das fontes oficiais. Só entra no corpus o que estiver aqui |
| `ingestao.py` | Baixa as páginas do Planalto e divide em trechos por artigo |
| `dados/brutos/` | Páginas originais, como vieram do site (para reproduzir o corpus) |
| `corpus/trechos.jsonl` | Um trecho por linha, com texto e metadados |
| `corpus/manifesto.json` | Versão do corpus (hash), data e contagem por fonte |
| `busca_lexical.py` | Busca BM25 com SQLite FTS5 (linha de base) |

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
