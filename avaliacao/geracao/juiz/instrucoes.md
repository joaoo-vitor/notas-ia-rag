# Instruções de avaliação das respostas

Valem igualmente para o juiz automático e para a anotação humana. Cada item tem:

- `pergunta`: o que a usuária escreveu.
- `resposta`: o texto gerado para ela. As marcas `[n]` remetem aos trechos fornecidos.
- `trechos_fornecidos`: os trechos de lei que o gerador recebeu, numerados como na resposta.
  Pode estar vazio (o gerador respondeu sem trechos).
- `referencia`: os trechos de lei que, segundo o gabarito, respondem à pergunta. Vazia quando
  a base de leis não responde à pergunta.

Você não sabe qual sistema gerou a resposta. Avalie cada item sozinho, sem comparar com os
outros.

## 1. Afirmações e fidelidade

Liste as afirmações da resposta sobre a lei, direitos, crimes, penas, medidas, órgãos ou
procedimentos. Ignore frases de acolhimento ("sinto muito", "você não está sozinha") e
recomendações genéricas sem conteúdo jurídico. Junte numa afirmação só o que for uma ideia só.

Para cada afirmação, um rótulo:

- `apoiada`: os trechos fornecidos dizem isso (pode estar em outras palavras).
- `nao_apoiada`: os trechos fornecidos não dizem isso, mesmo que seja verdade fora deles.
- `contradiz`: os trechos fornecidos dizem o contrário.

Se `trechos_fornecidos` estiver vazio, julgue as afirmações contra a `referencia` e, se ela também
estiver vazia, marque todas como `nao_apoiada`.

Não premie citação: uma afirmação apoiada pelo trecho [2] e marcada com [1] continua `apoiada`.
A citação errada é medida à parte, automaticamente.

## 2. Correção em relação à referência

- `correta`: responde à pergunta de forma compatível com a referência, sem afirmação jurídica
  errada.
- `parcial`: o essencial está certo, mas falta parte importante da referência ou há um erro
  secundário.
- `incorreta`: não responde ao que foi perguntado, responde outra coisa, ou contém erro
  jurídico relevante em relação à referência.
- `sem_referencia`: a `referencia` está vazia (a base não responde a essa pergunta).

## 3. Utilidade para a usuária (1 a 5)

Quanto a resposta ajuda essa usuária, considerando o que ela perguntou, clareza e linguagem
simples:

- 5: responde exatamente o que ela perguntou, claro e sem juridiquês.
- 4: responde, com pequenas falhas (um pouco longa, um termo técnico sem explicar).
- 3: responde em parte, ou responde mas é difícil de entender.
- 2: tangencia a pergunta; ela continuaria sem saber o que queria.
- 1: não ajuda ou pode confundir/prejudicar.

## Formato da saída

Uma linha JSON por item, nesta forma:

```json
{"item": "j0001", "afirmacoes": [{"texto": "destruir o celular é violência patrimonial", "rotulo": "apoiada"}], "correcao": "correta", "utilidade": 5, "comentario": "uma frase, só se ajudar a entender a nota"}
```
