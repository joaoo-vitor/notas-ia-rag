# Plano do RAG: recuperador, integração e geração

Rascunho para aprovação · atualizado em 20/11/2026 · apresentação final em 27/11/2026

## 1. Onde estamos

O RAG é dividido entre dois repositórios:

- **Recuperação, o R:** `notas-ia-rag`, que entra no `notas-ia` como submodule `rag/`.
- **Geração, o G:** fica no backend do `notas-ia` (`apps/backend/agent/`), que já tem o agente de
  conversa, o prompt e a validação de citações. A Aline liberou mudar essa parte.

O backend chama uma ferramenta MCP `search_legal_sources` que ainda não existe. Sem ela, o chat
roda **sem consultar nenhuma lei** (modo degradado).

| Tarefa | Pergunta que responde | Onde | Estado |
|---|---|---|---|
| **B. Melhora do recuperador** | Os trechos certos chegam ao topo? | `notas-ia-rag` | **concluída** |
| **C. Integração** | O chat do produto consulta o RAG? | `notas-ia-rag` (servidor MCP) e `notas-ia` (compose) | a fazer |
| **G. Geração no backend** | A LLM responde com base nos trechos, cita direito e recusa quando não sabe? | `notas-ia` (backend), por PR para a Aline | a fazer |

**Decisão:** a geração fica no backend, onde já está. O `notas-ia-rag` entrega a busca por um
servidor MCP. As correções da geração vão por PR no repositório da Aline.

---

## 2. Tarefa B: melhora do recuperador (concluída)

Os números estão no README do `notas-ia-rag`. Eles usam o gabarito `0cc27a5e4188`: 142 perguntas
com resposta e 23 sem.

- **Palavras-chave por trecho (`enriquecimento.yaml`):**
  - geradas pelo Gemini a partir só do texto da lei;
  - conferidas por um revisor sem acesso ao gabarito;
  - limpas pelo grupo, que só removeu termos.

  Melhoram todas as buscas, com diferença conclusiva. A melhor passa a ser a **híbrida BGE-M3**
  (BM25 + densa por RRF):

  | Métrica (por artigo) | Antes, melhor busca | Agora, híbrida BGE-M3 |
  |---|---|---|
  | acerto@1 | 0,51 | 0,72 |
  | acerto@3 | 0,73 | 0,92 |
  | acerto@5 | 0,82 | 0,94 |
  | MRR | 0,64 | 0,82 |

  As metas do plano anterior (acerto@5 por trecho ≥ 0,88 e MRR ≥ 0,70) foram atingidas:
  0,91 e 0,79.
- **Reranqueador:** descartado neste hardware.
  - O `mmarco` piorou a busca: MRR 0,64 → 0,52.
  - O `bge-reranker-v2-m3` leva ~23 s por pergunta em CPU.

  Fica como trabalho futuro, com GPU.
- **Reescrita da pergunta:** não feita; sai do escopo até 27/11.
- **Recusa pelo escore da busca:** não funciona.
  - Com o melhor limiar, a densa BGE-M3 recusa 3 das 23 perguntas sem resposta.
  - Na híbrida, o escore é uma soma de posições (RRF) e não mede confiança.

  A recusa fica com a geração (G3).

**Pendência opcional:** um conjunto de teste novo, de ~40 perguntas escritas por quem não viu os
ajustes, para números livres de vazamento. Se não houver tempo, a apresentação usa o gabarito
atual, com a ressalva de que a maior parte dele ainda é rascunho.

---

## 3. Tarefa C: integração (servidor MCP e compose)

### C1. Ponteiro do submodule

Atualizar `rag/` no `notas-ia` para o commit com as palavras-chave (`ffb1e1d`).

### C2. Servidor MCP jurídico, no `notas-ia-rag`

Pacote `legal_rag`, no formato que o `compose.yaml` e o backend já esperam:

- `python -m legal_rag index --strategy <nome>`: gera os índices (BM25 e, para a híbrida, o
  índice denso);
- `python -m legal_rag serve --host <h> --port <p>`: sobe o servidor MCP em `/mcp` com
  `fastmcp` (a mesma biblioteca do backend) e responde a `GET /health/ready`;
- **ferramenta `search_legal_sources(query, limit)`:** usa a híbrida BGE-M3. Se o índice denso
  não existir ou o modelo não carregar, cai para a BM25 e informa o motivo.

Resposta, no formato que `agent/runtime.py` já lê:

| Campo | Origem |
|---|---|
| `results[].chunk_id` | `id` do trecho |
| `results[].document` | `documento` |
| `results[].section` | `secao` |
| `results[].url` | `url` (link oficial com âncora do artigo) |
| `results[].verified_at` | `verificado_em` |
| `results[].text` | `texto` (só o texto oficial; as palavras-chave nunca vão para a LLM) |
| `corpus_version` | `corpus/manifesto.json` |
| `effective_strategy` | busca usada, ex.: `hibrida-bge-m3` ou `lexical` |
| `fallback_reason` | por que caiu para a BM25, ou `null` |

**Testes:** a ferramenta devolve os campos acima, e o fallback para a BM25 funciona sem o modelo
denso.

### C3. Compose e imagem

- **No `notas-ia-rag`:** criar um `Dockerfile`.
- **No `compose.yaml`:** trocar `./services/legal-mcp`, que não existe, por `./rag`, e
  `--strategy bm25` por `--strategy hibrida-bge-m3`.
- **Modelo:** a BGE-M3 tem ~2,3 GB e precisa de ~3 GB de RAM. O modelo é baixado na construção
  da imagem ou num volume, nunca a cada vez que o serviço sobe.
- **Se a máquina da demonstração não aguentar o BGE-M3:** usar `--strategy lexical`, que funciona
  com as palavras-chave (acerto@5 0,92 e MRR 0,75 por artigo), sem modelo de embeddings.

**Pronto quando:** `docker compose up` sobe o `legal-mcp` saudável, e o `/health/ready` do
backend deixa de dizer `degraded`.

---

## 4. Tarefa G: geração no backend (PR para a Aline)

Correções no `apps/backend`, em ordem de prioridade.

### G1. `num_ctx` no Ollama (bug que afeta o chat hoje)

`providers/http.py` não envia `num_ctx`. Sem isso, o Ollama usa o próprio padrão (2.048 ou 4.096
tokens), e não os 8.192 de `CONTEXT_BUDGET_TOKENS`. Se o prompt passar desse padrão, o Ollama
corta o começo sem avisar, e pode ir embora o prompt de sistema com as regras de segurança.
**Correção:** enviar `options.num_ctx` igual ao orçamento configurado.

### G2. Quantos trechos a LLM recebe

`TOOL_CONTEXT_BUDGET_TOKENS=1600` cabe ~3 a 5 trechos curtos. Com a híbrida, o artigo certo vem
até o 3º lugar em 92% das perguntas e até o 5º em 94%. **Proposta:** `limit=5` na busca e
orçamento de ~3.000 tokens, o que ainda cabe folgado nos 8.192 de contexto.

### G3. Recusa

Hoje há dois problemas:

- o prompt só menciona a recusa;
- `_validate_citations` trata resposta sem `[Fn]` como erro, então uma recusa correta vira a
  mensagem de falha.

**Proposta:**

- **Prompt:** responder só com base nos trechos. Se nenhum trecho tratar da pergunta, dizer que a
  lei consultada não trata disso e oferecer outro caminho: a rede de apoio, que já é uma
  ferramenta do backend, ou atendimento humano.
- **Recusa marcada:** a resposta de recusa leva uma marca fixa, ex.: começa com `[SEM_FONTE]`,
  que o backend remove antes de mostrar. Também daria por saída estruturada em JSON, mas a marca
  muda menos o runtime atual. Ver decisão 2.
- **Validador:** aceita resposta sem citação quando ela tem a marca de recusa. Continua
  rejeitando citação a trecho inexistente e afirmação jurídica sem citação.

### G4. De onde vem a busca

Hoje é a LLM que escreve a consulta da ferramenta, enquanto o gabarito mede a busca com a
mensagem da usuária. Duas opções:

- manter, que é mais simples e muda nada;
- **pré-busca** com a mensagem da usuária, mantendo a ferramenta para buscas extras.

**Proposta para 27/11:** manter e medir em G5. Só mudar se a busca escrita pela LLM for
claramente pior.

### G5. Avaliação de ponta a ponta

Um script no `notas-ia` manda perguntas do gabarito para a API do chat e mede:

- **recusa correta:** das 23 perguntas sem resposta, quantas o sistema recusa;
- **recusa indevida:** numa amostra das perguntas com resposta, quantas ele recusa sem dever;
- **citações válidas:** toda `[Fn]` aponta para um trecho que veio da busca;
- **citação certa:** o trecho citado está entre os relevantes ou parciais da pergunta no
  gabarito.

Com o modelo local na CPU, cada resposta leva segundos. Por isso, uma amostra (as 23 sem
resposta e ~40 com resposta) basta para a apresentação. A fidelidade com juiz-LLM e a revisão
humana ficam como trabalho futuro.

**Pronto quando:** o chat responde com citação em perguntas jurídicas, recusa nas perguntas fora
da lei, e existe uma tabela com os quatro números acima.

---

## 5. Ordem e calendário

| Datas | O quê | Resultado |
|---|---|---|
| 20/11 | Atualizar este plano; C1, o ponteiro | Plano aprovado |
| 21–22/11 | C2, o servidor MCP; C3, o Dockerfile e o compose | Chat consultando a lei |
| 22–23/11 | G1 e G2; G3, a recusa | PR para a Aline |
| 24–25/11 | G5, a avaliação de ponta a ponta | Tabela de recusa e citação |
| 25–26/11 | README, slides, folga | — |
| 27/11 | Apresentação | — |

**Prioridade se faltar tempo:**

1. C1 a C3 e G1 vêm antes de tudo: sem eles, o chat não consulta lei nenhuma.
2. Depois G3, a recusa, e G2.
3. G5 pode ficar reduzida às 23 perguntas sem resposta.
4. O conjunto de teste novo (seção 2) é o primeiro a sair.

---

## 6. Apoio: gabarito

- **Revisar os 134 rascunhos:** cada um precisa de duas pessoas que não sejam o autor. Enquanto
  isso, os números continuam provisórios.
- **Rótulos suspeitos da análise de erros:** q094 (injúria?), q123 e q141 (lesão corporal,
  CP 129, provavelmente deveria entrar).
- **Assédio sexual (CP 216-A):** fora do corpus por enquanto. O artigo trata de assédio no
  trabalho, fora do foco da Lei Maria da Penha.

---

## 7. Decisões para o aval

1. **Geração no backend**, com as correções G1 a G3 por PR para a Aline, e o `notas-ia-rag`
   entregando só a busca via MCP.
2. **Recusa (G3):** marca fixa no texto (proposta) ou saída estruturada em JSON?
3. **Trechos (G2):** `limit=5` e orçamento de ~3.000 tokens?
4. **Busca (G4):** manter a consulta escrita pela LLM até a medição de G5?
5. **Estratégia na demonstração (C3):** híbrida BGE-M3, ou BM25 com palavras-chave se a máquina
   não tiver ~3 GB de RAM livres?
6. **Conjunto de teste novo:** fazer até 27/11 ou deixar como trabalho futuro?
