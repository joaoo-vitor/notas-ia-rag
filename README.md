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

### Servidor MCP (integração com o backend)

O backend do Notas.ia consulta o RAG pelo servidor MCP do pacote `legal_rag`, com a
ferramenta `search_legal_sources(query, limit)` (para a LLM do backend,
`legal_search_legal_sources`):

```bash
.venv/Scripts/python -m legal_rag index --strategy hibrida-bge-m3   # BM25 + índice denso
.venv/Scripts/python -m legal_rag search "meu ex fica me seguindo"  # testa sem servidor
.venv/Scripts/python -m legal_rag serve --host 127.0.0.1 --port 8001  # MCP em /mcp
```

- `serve` usa a estratégia do último `index` (padrão `hibrida-bge-m3`; `bm25` usa só a BM25).
- Se o índice denso faltar ou for de outro corpus, ou o modelo não carregar, a busca cai
  para a BM25 e informa o motivo em `fallback_reason`. `GET /health/ready` mostra a
  estratégia em uso.
- A resposta traz `chunk_id`, `document`, `section`, `url`, `verified_at` e `text` (só o
  texto oficial; as palavras-chave nunca vão para a LLM), além de `corpus_version`,
  `effective_strategy` e `fallback_reason`.

## Arquivos

| Arquivo | O que é |
|---|---|
| `fontes.yaml` | Lista das fontes oficiais. Só entra no corpus o que estiver aqui |
| `ingestao.py` | Baixa as páginas do Planalto e divide em trechos por artigo |
| `enriquecimento.yaml` | Palavras-chave, expressões e categorias por trecho, usadas só no índice de busca |
| `dados/brutos/` | Páginas originais, como vieram do site (para reproduzir o corpus) |
| `corpus/trechos.jsonl` | Um trecho por linha, com texto e metadados |
| `corpus/manifesto.json` | Versão do corpus (hash), data e contagem por fonte |
| `busca_lexical.py` | Busca BM25 com SQLite FTS5 (linha de base) |
| `busca_densa.py` | Busca densa (embeddings + FAISS) e híbrida (BM25 + densa por RRF) |
| `gerar_palavras_chave.py` | Gera o `enriquecimento.yaml` com o Gemini, a partir só do texto da lei |
| `legal_rag/` | Servidor MCP que o backend consulta ([como rodar](#servidor-mcp-integração-com-o-backend)) |
| `reranqueador.py` | Reordena os candidatos da busca com um cross-encoder (experimento) |
| `avaliacao/gabarito.yaml` | Perguntas leigas com os artigos que as respondem |
| `avaliar.py` | Mede acerto@k, recall@k, MRR e nDCG@10 contra o gabarito |
| `test_rag.py`, `test_legal_rag.py` | Testes (`python -m unittest test_rag test_legal_rag`) |
| `comparar.py` | Compara recuperadores de uma mesma prova, com intervalos de confiança |
| `avaliacao/resultados/` | Resultado de cada avaliação, por recuperador e versão do corpus |
| `editor/` | Aplicação local para escrever e revisar o gabarito ([como usar](editor/README.md)) |

## Gabarito

O gabarito é a referência para comparar as buscas, então precisa ser feito por pessoas:

O jeito mais fácil é pelo **editor do gabarito**: `python editor/servidor.py` (ver
[editor/README.md](editor/README.md)). O processo:

1. Escrever a pergunta como uma usuária escreveria e indicar o `tipo`.
2. Apontar os artigos que respondem (`relevantes`) e os que ajudam (`parciais`).
   Quando um artigo tiver sido dividido em partes, abrir "Partes exatas de artigos
   divididos" e indicar quais trechos realmente contêm a resposta.
3. Duas pessoas conferem, sem ver a resposta uma da outra; divergências são discutidas.
4. Só então o `status` vira `revisado`. Por padrão, o `avaliar.py` usa só os revisados.

O gabarito tem agora 77 perguntas em **rascunho**: 31 iniciais, escritas com auxílio de
IA, e 46 escritas pelo grupo a partir de temas da legislação. Quem escreve uma pergunta
não é revisor dela. Meta: 150 a 300.

## Resultados (provisórios)

### Estado do teste

| | |
|---|---|
| Data | 20/11/2026 |
| Corpus | versão `b64ec663b6c4`: 101 trechos (98 de legislação + 3 de jurisprudência), 9 fontes; art. 7º da LMP dividido por inciso; palavras-chave revisadas em todos os trechos |
| Corpus sem palavras-chave | versão `174e8766c4b2` (mesmos trechos, sem `enriquecimento.yaml`) |
| Gabarito | versão `0cc27a5e4188`: 165 perguntas, 31 revisadas e 134 em rascunho |
| Perguntas usadas | 142 com resposta (78 coloquiais, 33 adversas, 31 diretas) e 23 sem resposta |
| Comandos | `avaliar.py --incluir-rascunho --recuperador <nome>` e `comparar.py b64ec663b6c4-0cc27a5e4188` |
| Arquivos | `avaliacao/resultados/<recuperador>-<corpus>-0cc27a5e4188.json` |

As versões são hashes do conteúdo (`corpus/manifesto.json` e `avaliacao/gabarito.yaml`):
qualquer mudança gera outra versão, e cada resultado fica num arquivo próprio. Como a maior
parte do gabarito ainda não foi revisada, os números servem para comparar as buscas entre
si, não como medida final.

### Avaliação por artigo (142 perguntas)

As partes de um artigo dividido (`-p1`, `-p2`...) contam como um só artigo. Em negrito, o
melhor valor de cada coluna. As e5 foram medidas só sem palavras-chave: a BGE-M3 já era
melhor ou empatada com elas.

| Recuperador | Palavras-chave | acerto@1 | acerto@3 | acerto@5 | acerto@10 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|---|---|
| BM25 (`lexical`) | não | 0,36 | 0,53 | 0,60 | 0,70 | 0,53 | 0,63 | 0,46 | 0,48 |
| Densa e5-small | não | 0,39 | 0,56 | 0,63 | 0,76 | 0,55 | 0,68 | 0,49 | 0,50 |
| Híbrida e5-small | não | 0,39 | 0,63 | 0,70 | 0,82 | 0,60 | 0,73 | 0,53 | 0,53 |
| Densa e5-base | não | 0,42 | 0,64 | 0,80 | 0,89 | 0,68 | 0,79 | 0,57 | 0,58 |
| Híbrida e5-base | não | 0,46 | 0,70 | 0,82 | 0,89 | 0,70 | 0,80 | 0,61 | 0,61 |
| Densa BGE-M3 | não | 0,51 | 0,73 | 0,82 | 0,90 | 0,72 | 0,81 | 0,64 | 0,64 |
| Híbrida BGE-M3 | não | 0,51 | 0,66 | 0,78 | 0,89 | 0,68 | 0,81 | 0,63 | 0,63 |
| BM25 (`lexical`) | sim | 0,62 | 0,87 | 0,92 | 0,94 | 0,81 | 0,88 | 0,75 | 0,74 |
| Densa BGE-M3 | sim | 0,65 | 0,82 | 0,91 | 0,96 | 0,81 | 0,90 | 0,76 | 0,75 |
| **Híbrida BGE-M3** | sim | **0,72** | **0,92** | **0,94** | **0,98** | **0,85** | **0,93** | **0,82** | **0,81** |

Por tipo de pergunta (acerto@1 / acerto@5, híbrida BGE-M3 com palavras-chave): diretas
0,84 / 1,00, coloquiais 0,71 / 0,92, adversas 0,64 / 0,91. Sem palavras-chave, a melhor
(densa BGE-M3) tinha coloquiais 0,49 / 0,76 e adversas 0,36 / 0,79.

### Avaliação por trecho (142 perguntas)

Cada parte de um artigo dividido conta separadamente: trazer o `art22-p2` quando a
resposta está no `art22-p1`, ou o inciso de violência física quando a pergunta é sobre
violência patrimonial, não é acerto. É a medida mais próxima do que a LLM vai ler. Todas
as 142 perguntas têm a parte certa indicada no gabarito.

| Recuperador | Palavras-chave | acerto@1 | acerto@3 | acerto@5 | acerto@10 | recall@5 | recall@10 | MRR | nDCG@10 |
|---|---|---|---|---|---|---|---|---|---|
| BM25 (`lexical`) | não | 0,36 | 0,52 | 0,58 | 0,66 | 0,50 | 0,59 | 0,46 | 0,46 |
| Densa e5-small | não | 0,38 | 0,53 | 0,61 | 0,73 | 0,51 | 0,63 | 0,48 | 0,48 |
| Híbrida e5-small | não | 0,38 | 0,60 | 0,68 | 0,78 | 0,57 | 0,68 | 0,51 | 0,50 |
| Densa e5-base | não | 0,40 | 0,61 | 0,72 | 0,87 | 0,60 | 0,75 | 0,54 | 0,55 |
| Híbrida e5-base | não | 0,45 | 0,66 | 0,77 | 0,87 | 0,64 | 0,76 | 0,59 | 0,59 |
| Densa BGE-M3 | não | 0,51 | 0,71 | 0,80 | 0,88 | 0,69 | 0,78 | 0,63 | 0,62 |
| Híbrida BGE-M3 | não | 0,50 | 0,65 | 0,77 | 0,87 | 0,65 | 0,76 | 0,62 | 0,60 |
| BM25 (`lexical`) | sim | 0,58 | 0,82 | 0,87 | 0,89 | 0,76 | 0,83 | 0,70 | 0,70 |
| Densa BGE-M3 | sim | 0,63 | 0,82 | 0,89 | 0,94 | 0,78 | 0,87 | 0,74 | 0,73 |
| **Híbrida BGE-M3** | sim | **0,68** | **0,88** | **0,91** | **0,95** | **0,81** | **0,89** | **0,79** | **0,77** |

### O que é conclusivo

`comparar.py` calcula intervalos de confiança de 95% por bootstrap pareado (as mesmas
perguntas sorteadas para todos os recuperadores). Para comparar versões diferentes do
corpus, o mesmo cálculo foi feito pergunta a pergunta entre os arquivos de resultado.

- **As palavras-chave melhoram todas as buscas, com diferença conclusiva.** MRR por trecho:
  BM25 0,46 → 0,70 (+0,25 [+0,17; +0,32]), densa BGE-M3 0,63 → 0,74 (+0,12 [+0,07; +0,17]),
  híbrida BGE-M3 0,62 → 0,79 (+0,17 [+0,11; +0,24]). O ganho é maior nas perguntas
  coloquiais e adversas, que eram o ponto fraco.
- **Com palavras-chave, a híbrida BGE-M3 passa a ser a melhor busca.** MRR por artigo 0,82
  [0,77; 0,87], à frente da densa BGE-M3 (−0,06 [−0,11; −0,02]) e da BM25 (−0,08
  [−0,12; −0,03]). No acerto@5 as três empatam dentro do intervalo. Sem palavras-chave, a
  densa BGE-M3 e a híbrida empatavam.
- **A revisão das palavras-chave não custou nada.** Comparando a saída bruta do Gemini
  (corpus `79ea83203953`) com a versão revisada (`b64ec663b6c4`), as diferenças ficam
  dentro de ±0,01; só o MRR da híbrida sobe um pouco (+0,01, conclusivo).
- **O reranqueador não compensou neste hardware.** O `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`
  piorou a busca (MRR por artigo 0,64 → 0,52 sobre a densa BGE-M3, conclusivo). O
  `BAAI/bge-reranker-v2-m3` leva cerca de 23 s por pergunta em CPU, inviável para um chat;
  só faria sentido com GPU. Fica como trabalho futuro: com a híbrida, o artigo certo já
  está entre os 3 primeiros em 92% das perguntas, e a LLM recebe os 3 a 5 primeiros.
- **A recusa não sai do escore da busca.** Com o melhor limiar possível, a BGE-M3 densa
  recusa só 3 das 23 perguntas sem resposta (sem recusar nenhuma com resposta; medido sem
  palavras-chave). A busca sempre devolve k trechos, então a recusa precisa vir da LLM
  (prompt e validação das citações no backend).

### Palavras-chave (`enriquecimento.yaml`)

Proposta do grupo: acrescentar a cada trecho termos no jeito como a usuária escreve, para
aproximar a linguagem coloquial da linguagem da lei. Como foram feitas:

1. `gerar_palavras_chave.py` pede ao Gemini (free tier), para cada trecho, palavras-chave,
   expressões em primeira pessoa e categorias de violência. O Gemini recebe **só o texto
   oficial do trecho**, nunca o gabarito.
2. Um revisor sem acesso ao gabarito conferiu cada termo contra o texto da lei e apontou
   os que afirmavam algo que o trecho não diz (ex.: "prisão" no art. 12-C, que trata de
   afastamento do lar), que vinham de outra norma ou que eram genéricos demais.
3. O grupo removeu os termos que fazia sentido remover (695 → 617 palavras-chave,
   474 → 451 expressões). Nada foi acrescentado, então nenhum termo veio do gabarito.

As categorias foram geradas, mas ainda não entram na busca nem foram revisadas. Cuidado
registrado: 15 das 142 perguntas têm uma expressão quase igual a alguma do enriquecimento
(na saída bruta); sem essas 15, o ganho no MRR continua (+0,13).

### Análise de erros (BGE-M3 densa, corpus `68fa3d5ea28f`)

Antes da divisão do art. 7º, a BGE-M3 densa não trazia nenhum artigo relevante nos 5
primeiros em 28 das 143 perguntas. Os erros se concentram em três causas:

- **Vocabulário diferente do da lei.** A pergunta diz "cadeia", "papel do hospital",
  "pegou meu celular"; a lei diz "prisão", "prontuário", "retenção de objetos". É o caso
  do art. 7º, relevante em 35 perguntas e presente nos 5 primeiros em só 4 (8 depois da
  divisão por inciso), e de perguntas sobre os arts. 9, 10-A e 21.
- **Artigos "ímã".** Os arts. 12-C e 12-D começam com uma frase genérica ("risco atual ou
  iminente à vida ou à integridade física, sexual, psicológica, moral ou patrimonial da
  mulher"). O 12-C aparece nos 5 primeiros de 68 das 143 perguntas e é relevante em 3; o
  12-D, em 45, e não é relevante em nenhuma. Há efeito parecido entre crimes vizinhos
  (estupro no lugar de cárcere privado, lesão corporal no lugar de importunação sexual).
- **Gabarito.** Poucos casos: uma pergunta sem texto (q132) e algumas com rótulos
  provavelmente incompletos (q094, q123, q141).

Em 23 das 28 falhas o artigo certo estava entre os 30 primeiros candidatos. As
palavras-chave atacaram a primeira causa: com elas, a híbrida BGE-M3 deixa de trazer o
artigo certo nos 5 primeiros em 9 das 142 perguntas.

Resultados de testes anteriores (outros corpus e gabaritos) continuam em
`avaliacao/resultados/`, com a versão no nome do arquivo, mas não são comparáveis com estas
tabelas.

## Decisões da ingestão

- **Um trecho por artigo**, com caput, incisos, parágrafos e penas. Artigos com mais de
  2.500 caracteres são divididos em partes que repetem o caput.
- **Um trecho por inciso** nos artigos listados em `dividir_por_inciso` (`fontes.yaml`).
  Hoje só o art. 7º da Lei Maria da Penha: cada parte (`art7-p1` a `art7-p6`) traz o caput
  e uma forma de violência (física, psicológica, sexual, patrimonial, moral, vicária), as
  mesmas categorias do classificador. Inteiro, o art. 7º misturava as seis e quase nunca
  era recuperado.
- **Enriquecimento** (`enriquecimento.yaml`): termos no jeito como a usuária escreve, que
  entram só no índice (BM25 e embeddings), nunca no texto entregue à LLM. Devem ser
  escritos a partir da lei e de fontes externas, **sem olhar o gabarito**. O enriquecimento
  entra no hash do trecho, então editá-lo muda a versão do corpus.
- **Texto revogado é descartado.** O Planalto mantém a redação antiga riscada
  (`<strike>` ou `line-through`); ela nunca entra no corpus.
- **Anotações de alteração** ("Incluído pela Lei nº ...") saem do texto e vão para o
  campo `alterado_por`.
- **Leis que só alteram outras leis** não entram: o texto já está na versão compilada.
- **Rubricas** do Código Penal ("Calúnia", "Lesão corporal") ficam no início do trecho,
  porque ajudam a busca.
- **Jurisprudência curta** pode ser extraída de páginas oficiais por uma frase distintiva
  declarada em `fontes.yaml`. A ingestão guarda o menor bloco que contém a frase e falha
  explicitamente se a página mudar, em vez de inserir silenciosamente outro conteúdo.

## Limitações conhecidas

- O corpus ainda não foi revisado por alguém da área jurídica.
- A busca lexical sozinha não liga linguagem coloquial à linguagem da lei ("fica me
  seguindo" não encontra "perseguição"); as palavras-chave e a busca densa cobrem parte
  disso.
- As palavras-chave foram geradas por LLM e revisadas pelo grupo, não por alguém da área
  jurídica.
- A busca sempre devolve k trechos, mesmo quando a lei não trata da pergunta.
- Ainda não há notas em linguagem simples nem cartilhas (só texto de lei).
