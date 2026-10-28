# Editor do gabarito

Aplicação local para escrever, consultar e revisar o gabarito do RAG
(`avaliacao/gabarito.yaml`) sem editar o YAML à mão.

## Como abrir

No Windows, dê dois cliques em `editor/iniciar.bat`. Ou, na raiz deste repositório
(em `notas-ia`, a pasta `rag/`):

```bash
python editor/servidor.py
```

O navegador abre em http://127.0.0.1:8765. Para fechar, feche a janela do terminal ou
aperte Ctrl+C nela.

**Requisitos:** só Python 3 (qualquer instalação; não precisa do `.venv`) e internet, porque a
página carrega o Vue e o js-yaml de um CDN. Funciona em qualquer navegador.

Opções:

```bash
python editor/servidor.py --porta 8800          # se a porta 8765 estiver ocupada
python editor/servidor.py --rag C:/outro/rag    # para usar outra cópia do repositório
python editor/servidor.py --sem-navegador       # não abre o navegador sozinho
```

## Passo a passo

1. **Coloque seu nome** no campo "Seu nome", no topo. Ele fica guardado no navegador.
2. **Nova pergunta**
   - Escreva a pergunta como uma usuária escreveria no chat, **antes** de procurar o artigo.
   - Escolha o **tipo** (a explicação de cada um aparece embaixo) e a **origem**: `grupo` para
     perguntas que você escreveu, `ia` para geradas por IA, `faq` ou `pesquisa` quando vierem
     dessas fontes.
   - Em **Relevantes**, digite o número do artigo (`147-A`), o nome da lei ou uma palavra do
     texto (`perseguir`, `pensão`) e escolha na lista (setas + Enter, ou clique).
   - **Parciais** são artigos que ajudam, mas não respondem sozinhos.
   - Clique em **Adicionar**. A pergunta entra no gabarito como `rascunho`.
3. **Trechos**
   - Lista todos os artigos do corpus. Filtre por palavra ou por lei e clique num artigo para
     ler o texto formatado.
   - **Adicionar trecho como resposta** (ou **como parcial**) manda o artigo para a pergunta
     que está sendo escrita na aba "Nova pergunta".
4. **Gabarito**
   - Todas as perguntas, editáveis: texto, tipo, origem, status e artigos.
   - Ao conferir uma pergunta, clique em **✓ Revisei**. Com dois revisores, ela passa
     sozinha para `revisado`.
   - O filtro **Falta a minha revisão** mostra só as perguntas que você ainda não conferiu.

### Regras para o gabarito valer

- Escreva a pergunta antes de procurar o artigo; senão ela copia as palavras da lei.
- Decida a resposta lendo o artigo, não rodando as buscas do RAG.
- Quem escreve uma pergunta não é um dos dois revisores dela.
- Na dúvida sobre o que a lei diz, deixe como `rascunho` e comente com o grupo.

## Como os dados são gravados

- Cada alteração é salva sozinha em ~1 segundo. O canto superior direito mostra
  "Salvo às…"; Ctrl+S salva na hora.
- Antes de gravar, o arquivo anterior é copiado para `avaliacao/gabarito.yaml.bak`
  (ignorado pelo git).
- Se o `gabarito.yaml` for alterado por fora com o editor aberto (por exemplo, por um
  `git pull`), o editor não sobrescreve e pede para recarregar a página.
- Os comentários do topo do arquivo são preservados. Comentários **entre** as perguntas
  somem na primeira gravação.
- A pergunta que está sendo escrita fica guardada no navegador, mesmo se a página fechar.

## Trabalhando em grupo

O gabarito é um arquivo só, então duas pessoas editando ao mesmo tempo em máquinas
diferentes vão gerar conflito no git. Para evitar:

1. `git pull` antes de abrir o editor.
2. Trabalhe, feche o editor e faça commit e push logo em seguida.
3. Se o push for recusado, `git pull`, resolva o conflito no `gabarito.yaml` (em geral,
   manter as perguntas dos dois lados) e faça push de novo.

## Arquivos

| Arquivo | O que é |
|---|---|
| `servidor.py` | Servidor local: entrega a página e lê/grava o corpus e o gabarito |
| `index.html` | Estrutura das três abas |
| `app.js` | Lógica da página (Vue 3), sugestão de artigos e gravação do YAML |
| `estilo.css` | Aparência |
| `iniciar.bat` | Atalho para abrir no Windows |
