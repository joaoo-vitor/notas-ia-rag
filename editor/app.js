const { createApp, reactive, ref, computed, watch } = Vue;

const TIPOS = [
  { valor: "coloquial", rotulo: "coloquial", ajuda: "Linguagem do dia a dia: \"ele pega meu salário e não deixa eu trabalhar\"." },
  { valor: "direta", rotulo: "direta", ajuda: "Usa termos da lei: \"o que é medida protetiva de urgência?\"." },
  { valor: "adversa", rotulo: "adversa", ajuda: "Gíria, sem acento, abreviação ou erro de digitação: \"q acontece c/ quem bate na mulher\"." },
  { valor: "sem_resposta", rotulo: "sem_resposta", ajuda: "A base não responde (o sistema deve recusar). Deixe os relevantes vazios." },
];
const ORIGENS = ["grupo", "ia", "faq", "pesquisa"];
const ORDEM_CAMPOS = ["id", "pergunta", "tipo", "relevantes", "parciais", "trechos_relevantes", "trechos_parciais", "status", "revisores", "origem"];

// "lei-11340-2006:art22-p2" -> "lei-11340-2006:art22"
const artigoDe = (id) => id.replace(/-p\d+$/, "");

// "Lei nº 11.340/2006 (Lei Maria da Penha)" -> "Lei Maria da Penha"
function nomeCurto(documento) {
  const m = documento.match(/\(([^)]+)\)\s*$/);
  return m ? m[1] : documento;
}

function rotulo(trecho) {
  return `${nomeCurto(trecho.documento)} · ${trecho.secao.replace(/ \(parte.*\)$/, "")}`;
}

function semAcento(texto) {
  return texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

// ------------------------------------------------------------ YAML

function escalar(valor) {
  return jsyaml.dump(valor, { lineWidth: -1 }).trimEnd();
}

function serializar(cabecalho, perguntas) {
  const itens = perguntas.map((p) => {
    const linhas = [];
    for (const campo of ORDEM_CAMPOS) {
      const v = p[campo];
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) {
        if (!v.length && campo !== "relevantes") continue; // relevantes: [] marca "sem resposta"
        linhas.push(`${campo}: [${v.map(escalar).join(", ")}]`);
      } else {
        linhas.push(`${campo}: ${escalar(v)}`);
      }
    }
    // campos que o editor não conhece são mantidos
    for (const [campo, v] of Object.entries(p)) {
      if (!ORDEM_CAMPOS.includes(campo) && v !== undefined) linhas.push(`${campo}: ${escalar(v)}`);
    }
    return "  - " + linhas.join("\n    ");
  });
  return `${cabecalho}perguntas:\n${itens.join("\n")}\n`;
}

// ------------------------------------------------------------ campo de artigos com sugestões

const CampoArtigos = {
  props: { modelValue: { type: Array, default: () => [] }, artigos: Array, desabilitado: Boolean },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const texto = ref("");
    const aberto = ref(false);
    const destaque = ref(0);
    const porId = computed(() => Object.fromEntries(props.artigos.map((a) => [a.id, a])));

    const sugestoes = computed(() => {
      const termos = semAcento(texto.value).split(/\s+/).filter(Boolean);
      if (!termos.length) return [];
      return props.artigos
        .filter((a) => !props.modelValue.includes(a.id))
        .map((a) => {
          const alvo = a.busca;
          if (!termos.every((t) => alvo.includes(t))) return null;
          // artigo e nome da lei pesam mais que o texto
          const peso = termos.filter((t) => semAcento(a.rotulo + " " + a.id).includes(t)).length;
          return { a, peso };
        })
        .filter(Boolean)
        .sort((x, y) => y.peso - x.peso)
        .slice(0, 8)
        .map((x) => x.a);
    });

    function adicionar(a) {
      if (!a) return;
      emit("update:modelValue", [...props.modelValue, a.id]);
      texto.value = "";
      destaque.value = 0;
    }
    function remover(id) {
      emit("update:modelValue", props.modelValue.filter((x) => x !== id));
    }
    function tecla(e) {
      if (e.key === "ArrowDown") { destaque.value = Math.min(destaque.value + 1, sugestoes.value.length - 1); e.preventDefault(); }
      else if (e.key === "ArrowUp") { destaque.value = Math.max(destaque.value - 1, 0); e.preventDefault(); }
      else if (e.key === "Enter") { adicionar(sugestoes.value[destaque.value]); e.preventDefault(); }
      else if (e.key === "Escape") { aberto.value = false; }
      else if (e.key === "Backspace" && !texto.value && props.modelValue.length) { remover(props.modelValue.at(-1)); }
    }
    watch(texto, () => { destaque.value = 0; aberto.value = true; });
    const fechar = () => setTimeout(() => (aberto.value = false), 150); // deixa o clique na sugestão acontecer

    return { texto, aberto, destaque, sugestoes, porId, adicionar, remover, tecla, fechar };
  },
  template: `
    <div class="campo-artigos" :class="{ desabilitado }">
      <span v-for="id in modelValue" :key="id" class="chip" :title="porId[id] ? porId[id].previa : 'artigo não existe no corpus'"
            :class="{ invalido: !porId[id] }">
        {{ porId[id] ? porId[id].rotulo : id }}
        <button @click="remover(id)" title="Remover">×</button>
      </span>
      <div class="entrada">
        <input v-model="texto" :disabled="desabilitado" @keydown="tecla" @focus="aberto = true"
               @blur="fechar"
               :placeholder="desabilitado ? 'sem resposta: deixe vazio' : 'digite artigo, lei ou palavra do texto (ex.: 147-A, perseguir, pensão)'">
        <ul v-if="aberto && sugestoes.length" class="sugestoes">
          <li v-for="(a, i) in sugestoes" :key="a.id" :class="{ destaque: i === destaque }" @mousedown.prevent="adicionar(a)">
            <b>{{ a.rotulo }}</b> <code>{{ a.id }}</code>
            <span>{{ a.previa }}</span>
          </li>
        </ul>
      </div>
    </div>`,
};

// ------------------------------------------------------------ aplicação

createApp({
  components: { CampoArtigos },
  setup() {
    const aba = ref("nova");
    const carregado = ref(false);
    const estado = reactive({ texto: "Carregando…", tipo: "" });
    const nome = ref(localStorage.getItem("gabarito-nome") || "");
    watch(nome, (v) => localStorage.setItem("gabarito-nome", v));

    const trechos = ref([]);
    const perguntas = ref([]);
    let cabecalho = "";
    let versao = null;

    // um artigo pode ter várias partes (-p1, -p2): para o gabarito conta o artigo
    const artigos = computed(() => {
      const mapa = new Map();
      for (const t of trechos.value) {
        const id = artigoDe(t.id);
        if (!mapa.has(id)) mapa.set(id, { id, rotulo: rotulo(t), previa: "", busca: "" });
        const a = mapa.get(id);
        a.previa = a.previa || t.texto.split("\n").find((l) => /^(Art|Artigo)/.test(l)) || t.texto.slice(0, 160);
        a.busca += " " + semAcento(`${a.rotulo} ${id} ${t.texto}`);
      }
      return [...mapa.values()];
    });

    const opcoesTrechos = computed(() => trechos.value.map((t) => ({
      id: t.id,
      rotulo: `${rotulo(t)}${/-p\d+$/.test(t.id) ? ` · ${t.secao.match(/parte [^)]+/)?.[0] || t.id}` : ""}`,
      previa: t.texto.slice(0, 220),
      busca: semAcento(`${rotulo(t)} ${t.id} ${t.texto}`),
    })));

    // ---------------- carregar e salvar
    async function carregar() {
      const r = await fetch("/api/dados");
      const dados = await r.json();
      trechos.value = dados.trechos.split("\n").filter(Boolean).map((l) => JSON.parse(l));
      const i = dados.gabarito.search(/^perguntas:/m);
      cabecalho = i >= 0 ? dados.gabarito.slice(0, i) : dados.gabarito;
      const doc = jsyaml.load(dados.gabarito) || {};
      perguntas.value = (doc.perguntas || []).map((p) => ({
        ...p, relevantes: p.relevantes || [], parciais: p.parciais || [],
        trechos_relevantes: p.trechos_relevantes || [], trechos_parciais: p.trechos_parciais || [],
        revisores: p.revisores || [],
      }));
      versao = dados.versao;
      carregado.value = true;
      estado.texto = "Tudo salvo";
      estado.tipo = "ok";
    }

    let timer = null;
    function alterou() {
      estado.texto = "Salvando…";
      estado.tipo = "pendente";
      clearTimeout(timer);
      timer = setTimeout(salvar, 1200);
    }
    async function salvar() {
      clearTimeout(timer);
      const texto = serializar(cabecalho, perguntas.value);
      const r = await fetch("/api/gabarito", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ texto, versao }),
      });
      if (r.status === 409) {
        estado.texto = "Arquivo mudou fora do editor: recarregue";
        estado.tipo = "erro";
        return;
      }
      if (!r.ok) { estado.texto = "Erro ao salvar"; estado.tipo = "erro"; return; }
      versao = (await r.json()).versao;
      estado.texto = `Salvo às ${new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
      estado.tipo = "ok";
    }
    window.addEventListener("beforeunload", (e) => { if (estado.tipo === "pendente") e.preventDefault(); });
    window.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); salvar(); } });

    // ---------------- nova pergunta
    const rascunhoVazio = () => ({ pergunta: "", tipo: "coloquial", origem: "grupo", relevantes: [], parciais: [] });
    const rascunho = reactive(JSON.parse(localStorage.getItem("gabarito-rascunho") || "null") || rascunhoVazio());
    watch(rascunho, (v) => localStorage.setItem("gabarito-rascunho", JSON.stringify(v)), { deep: true });
    const erroRascunho = ref("");

    function proximoId() {
      const nums = perguntas.value.map((p) => parseInt(String(p.id).replace(/\D/g, ""), 10)).filter((n) => !isNaN(n));
      return "q" + String((nums.length ? Math.max(...nums) : 0) + 1).padStart(3, "0");
    }
    function adicionar() {
      erroRascunho.value = "";
      if (!rascunho.pergunta.trim()) return (erroRascunho.value = "Escreva a pergunta.");
      if (rascunho.tipo === "sem_resposta" && (rascunho.relevantes.length || rascunho.parciais.length))
        return (erroRascunho.value = "Pergunta sem resposta não pode ter artigos relevantes.");
      if (rascunho.tipo !== "sem_resposta" && !rascunho.relevantes.length)
        return (erroRascunho.value = "Indique pelo menos um artigo relevante (ou mude o tipo para sem_resposta).");
      if (perguntas.value.some((p) => semAcento(p.pergunta) === semAcento(rascunho.pergunta.trim())))
        return (erroRascunho.value = "Essa pergunta já está no gabarito.");
      perguntas.value.push({
        id: proximoId(), pergunta: rascunho.pergunta.trim(), tipo: rascunho.tipo,
        relevantes: [...rascunho.relevantes], parciais: [...rascunho.parciais],
        trechos_relevantes: [], trechos_parciais: [],
        status: "rascunho", revisores: [], origem: rascunho.origem,
      });
      limparRascunho();
      alterou();
    }
    function limparRascunho() {
      Object.assign(rascunho, rascunhoVazio(), { origem: rascunho.origem });
      erroRascunho.value = "";
    }

    // ---------------- trechos
    const trechoAtual = ref(null);
    const filtroTrecho = ref("");
    const filtroDocumento = ref("");
    const documentos = computed(() => [...new Set(trechos.value.map((t) => nomeCurto(t.documento)))]);
    const trechosFiltrados = computed(() => {
      const termos = semAcento(filtroTrecho.value).split(/\s+/).filter(Boolean);
      return trechos.value.filter((t) => {
        if (filtroDocumento.value && nomeCurto(t.documento) !== filtroDocumento.value) return false;
        const alvo = semAcento(`${rotulo(t)} ${t.id} ${t.texto} ${t.contexto}`);
        return termos.every((x) => alvo.includes(x));
      });
    });
    function noRascunho(t) {
      const id = artigoDe(t.id);
      return rascunho.relevantes.includes(id) ? "relevantes" : rascunho.parciais.includes(id) ? "parciais" : null;
    }
    function usarTrecho(lista) {
      const id = artigoDe(trechoAtual.value.id);
      const outra = lista === "relevantes" ? "parciais" : "relevantes";
      rascunho[outra] = rascunho[outra].filter((x) => x !== id);
      if (!rascunho[lista].includes(id)) rascunho[lista].push(id);
      if (rascunho.tipo === "sem_resposta") rascunho.tipo = "coloquial";
    }
    function classeLinha(linha) {
      if (/^(Art\.|Artigo)/.test(linha)) return "caput";
      if (/^§|^Parágrafo único/.test(linha)) return "paragrafo";
      if (/^[IVXLC]+\s*[-–]/.test(linha)) return "inciso";
      if (/^[a-z]\)/.test(linha)) return "alinea";
      if (/^Pena/.test(linha)) return "pena";
      return "rubrica";
    }

    // ---------------- gabarito
    const filtroPergunta = ref("");
    const filtroStatus = ref("");
    const filtroTipo = ref("");
    const perguntasFiltradas = computed(() =>
      perguntas.value.filter((p) => {
        if (filtroTipo.value && p.tipo !== filtroTipo.value) return false;
        if (filtroStatus.value === "falta-eu") { if (!nome.value || p.revisores.includes(nome.value)) return false; }
        else if (filtroStatus.value && p.status !== filtroStatus.value) return false;
        return semAcento(p.pergunta).includes(semAcento(filtroPergunta.value));
      })
    );
    const contar = (f) => perguntas.value.filter(f).length;
    function revisei(p) {
      p.revisores.push(nome.value);
      if (p.revisores.length >= 2) p.status = "revisado"; // regra do gabarito: duas pessoas conferem
      alterou();
    }
    function tirarRevisor(p, r) {
      p.revisores = p.revisores.filter((x) => x !== r);
      if (p.revisores.length < 2) p.status = "rascunho";
      alterou();
    }
    function remover(p) {
      if (!confirm(`Apagar a pergunta ${p.id}?\n\n"${p.pergunta}"`)) return;
      perguntas.value = perguntas.value.filter((x) => x !== p);
      alterou();
    }

    carregar().catch((e) => { estado.texto = "Não consegui carregar os dados: " + e.message; estado.tipo = "erro"; });

    return {
      TIPOS, ORIGENS, aba, carregado, estado, nome, trechos, perguntas, artigos, opcoesTrechos,
      rascunho, erroRascunho, adicionar, limparRascunho,
      trechoAtual, filtroTrecho, filtroDocumento, documentos, trechosFiltrados, noRascunho, usarTrecho, classeLinha,
      filtroPergunta, filtroStatus, filtroTipo, perguntasFiltradas, contar, revisei, tirarRevisor, remover,
      alterou, artigoDe, curto: (t) => `${nomeCurto(t.documento)} · ${t.secao}`,
      primeiraFrase: (t) => (t.texto.split("\n").find((l) => /^(Art|Artigo)/.test(l)) || "").replace(/^(Art\.|Artigo)\s*[\dA-Zº°o-]+\.?\s*-?\s*/, "").slice(0, 90),
    };
  },
}).mount("#app");
