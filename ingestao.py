"""Ingestão do corpus jurídico: baixa as fontes oficiais e divide em trechos por artigo.

Uso (na raiz deste repositório):
    .venv/Scripts/python ingestao.py            # baixa o que faltar e gera o corpus
    .venv/Scripts/python ingestao.py --baixar   # força baixar tudo de novo

Saídas:
    dados/brutos/<id>.htm      página original, como veio do site
    corpus/trechos.jsonl       um trecho por linha, com metadados
    corpus/manifesto.json      versão do corpus, fontes e contagens
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

RAIZ = Path(__file__).resolve().parent
BRUTOS = RAIZ / "dados" / "brutos"
CORPUS = RAIZ / "corpus"
TAMANHO_MAXIMO = 2500  # caracteres; artigos maiores são divididos mantendo o caput

# "(Incluído pela Lei nº 13.641, de 2018)", "(Redação dada pela ...)", "(Vide ...)", "(Vigência)" etc.
ANOTACAO = re.compile(
    r"\((?:Incluíd[oa]|Redação dada|Acrescid[oa]|Renumerad[oa]|Revogad[oa]|Vide|Vigência|Promulgação|Regulamento)[^()]*\)",
    re.IGNORECASE,
)
# "Art. 7º", "Art. 24-A.", "Art. 138 - Caluniar", e "Artigo 1" (tratados, como a Convenção de Belém do Pará)
INICIO_ARTIGO = re.compile(r"^(?:Art\.|Artigo)\s*(\d+)(?:\s*[º°o](?![a-zà-ú]))?(-[A-Z])?(?![a-zà-ú])\s*[.\-–]?\s*")
CABECALHO = re.compile(r"^(T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|LIVRO|PARTE)\s+[IVXLC\dÚU]", re.IGNORECASE)
FIM_DO_TEXTO = re.compile(r"^Brasília,\s*\d|^Rio de Janeiro,\s*\d")


# ---------------------------------------------------------------- download

def baixar(fonte, forcar=False):
    destino = BRUTOS / f"{fonte['id']}.htm"
    if destino.exists() and not forcar:
        return destino.read_bytes()
    req = urllib.request.Request(fonte["url"], headers={"User-Agent": "Mozilla/5.0 (notas-ia, pesquisa academica)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        dados = resp.read()
    BRUTOS.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(dados)
    print(f"  baixado: {fonte['id']} ({len(dados) / 1024:.0f} KB)")
    return dados


def decodificar(dados):
    """O Planalto serve páginas em UTF-16, UTF-8 ou windows-1252, conforme a página."""
    if dados[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return dados.decode("utf-16", errors="replace")
    cabeca = dados[:2000].decode("ascii", errors="ignore").lower()
    m = re.search(r"charset=[\"']?([\w-]+)", cabeca)
    try:
        return dados.decode(m.group(1) if m else "utf-8")
    except (UnicodeDecodeError, LookupError):
        return dados.decode("windows-1252", errors="replace")


# ---------------------------------------------------------------- extração

def limpar(texto):
    texto = texto.replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip()


def remover_revogados(sopa):
    """Texto revogado aparece riscado: <strike>, <s>, <del> ou estilo line-through."""
    for tag in sopa.find_all(["strike", "s", "del"]):
        tag.decompose()
    for tag in sopa.find_all(style=re.compile(r"line-through", re.I)):
        tag.decompose()


BLOCOS = ["p", "div", "br", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "center", "table"]


def paragrafos(html):
    """Linhas de texto na ordem do documento.

    Não basta ler os <p>: o HTML do Planalto é malformado e parte dos artigos
    (ex.: art. 121 e 129 do Código Penal) fica fora de qualquer parágrafo.
    """
    sopa = BeautifulSoup(html, "html.parser")  # o lxml perde trechos da Constituição
    for tag in sopa(["script", "style", "head"]):
        tag.decompose()
    remover_revogados(sopa)
    for s in sopa.find_all(string=True):  # quebras de linha do código-fonte não são quebras de texto
        s.replace_with(re.sub(r"\s+", " ", s))
    for tag in sopa.find_all(BLOCOS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for linha in sopa.get_text().split("\n"):
        texto = limpar(linha)
        if texto:
            yield texto


def normalizar_busca(texto):
    """Normaliza uma frase para localizar trechos mesmo com diferenças de acentuação."""
    texto = unicodedata.normalize("NFKD", limpar(texto).casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def extrair_trechos_textuais(html, selecoes):
    """Extrai pequenos blocos declarados em ``fontes.yaml``.

    É usado para decisões, informativos e notícias institucionais que não são
    organizados em artigos. Cada seleção informa uma frase distintiva em ``contem``;
    fica o menor parágrafo/bloco visível que contém a frase. Se a página mudar e a
    frase desaparecer, a ingestão falha em vez de produzir um trecho incorreto.
    """
    sopa = BeautifulSoup(html, "html.parser")
    for tag in sopa(["script", "style", "head", "nav", "footer"]):
        tag.decompose()

    candidatos = []
    for tag in sopa.find_all(["p", "li", "blockquote", "h1", "h2", "h3", "h4"]):
        texto = limpar(tag.get_text(" ", strip=True))
        if texto:
            candidatos.append(texto)
    # Fallback para páginas oficiais com HTML malformado ou texto fora de <p>.
    candidatos.extend(paragrafos(html))
    candidatos = list(dict.fromkeys(candidatos))

    resultado = []
    for selecao in selecoes:
        agulha = normalizar_busca(selecao["contem"])
        encontrados = [t for t in candidatos if agulha in normalizar_busca(t)]
        if not encontrados:
            raise ValueError(
                f"trecho {selecao['id']!r} não encontrado; a fonte pode ter mudado "
                f"(busca: {selecao['contem']!r})"
            )
        texto = min(encontrados, key=len)
        resultado.append({
            "id": selecao["id"],
            "secao": selecao["secao"],
            "contexto": selecao.get("contexto", ""),
            "texto": texto,
        })
    return resultado


def extrair_artigos(html):
    """Agrupa os parágrafos em artigos: caput + incisos + parágrafos + alíneas + penas."""
    artigos, atual, contexto, cabecalho_pendente = [], None, {}, []
    rubrica, anexo = None, False
    for texto in paragrafos(html):
        if FIM_DO_TEXTO.match(texto):
            # assinatura: o que vem depois só interessa se houver um anexo (ex.: texto de uma convenção)
            atual, rubrica = None, None
            continue
        if re.fullmatch(r"ANEXO( [IVX\d]+)?", texto, re.IGNORECASE):
            anexo, atual, contexto, cabecalho_pendente = True, None, {0: "Anexo"}, []
            continue
        alteracoes = [limpar(a)[1:-1] for a in ANOTACAO.findall(texto)]
        texto = limpar(ANOTACAO.sub("", texto))
        if not texto:
            continue

        m = INICIO_ARTIGO.match(texto)
        if m:
            numero = ("anexo-" if anexo else "") + m.group(1) + (m.group(2) or "")
            linhas = ([rubrica] if rubrica else []) + [texto]
            atual = {"numero": numero, "linhas": linhas, "alteracoes": alteracoes,
                     "contexto": " > ".join(v for v in contexto.values() if v)}
            artigos.append(atual)
            cabecalho_pendente, rubrica = [], None
            continue

        if rubrica is not None and atual is not None:
            # a rubrica não abriu um artigo novo: era um subtítulo dentro do artigo atual
            atual["linhas"].append(rubrica)
        rubrica = None

        if CABECALHO.match(texto):
            nivel = CABECALHO.match(texto).group(1).upper().replace("Í", "I").replace("SECAO", "SEÇÃO")
            nivel = {"PARTE": 0, "LIVRO": 1, "TITULO": 2, "CAPITULO": 3, "SEÇÃO": 4, "SUBSEÇÃO": 5}.get(nivel, 4)
            contexto = {k: v for k, v in contexto.items() if k < nivel}
            contexto[nivel] = texto
            cabecalho_pendente = [nivel]
            atual = None
            continue

        if cabecalho_pendente and atual is None and len(texto) < 150 and not re.match(r"^[IVXLC]+\s*[-–]", texto):
            # nome do título/capítulo vem no parágrafo seguinte ("DAS MEDIDAS PROTETIVAS DE URGÊNCIA")
            nivel = cabecalho_pendente[0]
            contexto[nivel] = f"{contexto[nivel]} – {texto}"
            cabecalho_pendente = []
            continue

        if eh_rubrica(texto):
            # nome do crime ou do tema antes do artigo ("Calúnia", "Lesão corporal")
            rubrica = texto
            continue

        if atual is not None:
            atual["linhas"].append(texto)
            atual["alteracoes"] += alteracoes
    return artigos


def eh_rubrica(texto):
    """Linha curta sem pontuação final que não é inciso, parágrafo, alínea nem pena."""
    if len(texto) > 80 or texto[-1] in ".;:,)":
        return False
    return not re.match(r"^([IVXLC]+\s*[-–]|§|[a-z]\)|Pena|Parágrafo único|\d+[.)])", texto)


SO_NUMERO = re.compile(r"^(§\s*\d+\s*[º°o]?|[IVXLC]+|[a-z]\)|Parágrafo único)\s*[.\-–]?\s*$")


VETADO = re.compile(r"^(§\s*\d+\s*[º°o]?|[IVXLC]+\s*[-–]|[a-z]\)|Parágrafo único\.?)\s*\(?\s*(VETADO|Revogad[oa])\b.*$", re.I)


def limpar_artigo(artigo):
    """Tira numerações que ficaram sem texto quando a redação revogada foi removida ("§ 5º .")
    e dispositivos vetados ou revogados dentro do artigo ("§ 1º (VETADO).")."""
    artigo["linhas"] = [l for l in artigo["linhas"] if not SO_NUMERO.match(l) and not VETADO.match(l)]
    return artigo


def descartar(artigo):
    """Artigos que não acrescentam conteúdo ao corpus."""
    linhas = artigo["linhas"]
    caput = next((l for l in linhas if INICIO_ARTIGO.match(l)), linhas[0])
    corpo = INICIO_ARTIGO.sub("", caput)
    if len(linhas) == 1 and re.fullmatch(r"\(?\s*(revogad[oa]|vetad[oa])\b.*", corpo, re.I):
        return True
    if re.match(r"^[“\"]", caput) or re.match(r"^[“\"]", linhas[0]):  # artigo citado dentro de uma lei que altera outra
        return True
    if re.search(r"passa(m)? a vigorar (com|acrescid)", corpo):  # lei de alteração: o texto já está na lei compilada
        return True
    return re.fullmatch(r"Esta (Lei|Emenda) entra (em )?vigor.*", corpo) is not None


def dividir(linhas):
    """Divide artigos longos em partes que repetem o caput, para não perder o contexto."""
    texto = "\n".join(linhas)
    if len(texto) <= TAMANHO_MAXIMO:
        return [texto]
    inicio = next(i for i, l in enumerate(linhas) if INICIO_ARTIGO.match(l))  # pula a rubrica, se houver
    caput, partes, atual = linhas[inicio], [], linhas[:inicio + 1]
    for linha in linhas[inicio + 1:]:
        if len("\n".join(atual + [linha])) > TAMANHO_MAXIMO and len(atual) > inicio + 1:
            # um subtítulo ("Violência Doméstica") vai junto com o parágrafo que ele anuncia
            levar = [atual.pop()] if eh_rubrica(atual[-1]) else []
            partes.append("\n".join(atual))
            atual = [caput + " (continuação)"] + levar
        atual.append(linha)
    partes.append("\n".join(atual))
    return partes


def data_no_titulo(html):
    """Lê a data do título da norma ("LEI Nº 11.340, DE 7 DE AGOSTO DE 2006") para conferir o fontes.yaml."""
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
             "agosto", "setembro", "outubro", "novembro", "dezembro"]
    m = re.search(r"\bDE\s+(\d{1,2})[º°o]?\s+DE\s+([A-ZÇÃ]+)\s+DE\s+(\d{4})", limpar(BeautifulSoup(html, "lxml").get_text(" "))[:3000], re.I)
    if not m or m.group(2).lower() not in meses:
        return None
    return f"{m.group(3)}-{meses.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"


# ---------------------------------------------------------------- corpus

def gerar(forcar_download=False):
    fontes = yaml.safe_load((RAIZ / "fontes.yaml").read_text(encoding="utf-8"))["fontes"]
    hoje = dt.date.today().isoformat()
    trechos, resumo = [], []

    for fonte in fontes:
        dados = baixar(fonte, forcar_download)
        html = decodificar(dados)

        if fonte.get("extracao") == "trechos_textuais":
            selecionados = extrair_trechos_textuais(html, fonte.get("trechos", []))
            for trecho in selecionados:
                texto = trecho["texto"]
                trechos.append({
                    "id": f"{fonte['id']}:{trecho['id']}",
                    "documento": fonte["documento"],
                    "secao": trecho["secao"],
                    "contexto": trecho["contexto"],
                    "texto": texto,
                    "orgao": fonte["orgao"],
                    "tipo": fonte["tipo"],
                    "jurisdicao": fonte["jurisdicao"],
                    "localidade": None,
                    "url": fonte["url"],
                    "publicado_em": fonte["publicado_em"],
                    "verificado_em": hoje,
                    "alterado_por": [],
                    "fonte_hash": hashlib.sha256(dados).hexdigest(),
                    "hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                })
            resumo.append({
                "id": fonte["id"], "artigos": 0, "trechos": len(selecionados)
            })
            print(f"  {fonte['id']}: {len(selecionados)} trechos selecionados")
            continue

        artigos = [limpar_artigo(a) for a in extrair_artigos(html)]
        artigos = [a for a in artigos if not descartar(a)]

        if fonte.get("artigos"):
            pedidos = set(fonte["artigos"])
            artigos = [a for a in artigos if a["numero"] in pedidos]
            faltando = pedidos - {a["numero"] for a in artigos}
            if faltando:
                print(f"  AVISO {fonte['id']}: artigos não encontrados: {sorted(faltando)}")

        vistos, unicos = set(), []
        for a in artigos:  # páginas compiladas às vezes repetem um artigo; fica a última redação
            if a["numero"] in vistos:
                unicos = [u for u in unicos if u["numero"] != a["numero"]]
            vistos.add(a["numero"])
            unicos.append(a)

        data = data_no_titulo(html)
        if data and data != fonte["publicado_em"]:
            print(f"  AVISO {fonte['id']}: data no título ({data}) difere do fontes.yaml ({fonte['publicado_em']})")

        for a in unicos:
            partes = dividir(a["linhas"])
            for i, texto in enumerate(partes, 1):
                sufixo = f"-p{i}" if len(partes) > 1 else ""
                trechos.append({
                    "id": f"{fonte['id']}:art{a['numero']}{sufixo}",
                    "documento": fonte["documento"],
                    "secao": f"Art. {a['numero']}" + (f" (parte {i}/{len(partes)})" if len(partes) > 1 else ""),
                    "contexto": a["contexto"],
                    "texto": texto,
                    "orgao": fonte["orgao"],
                    "tipo": fonte["tipo"],
                    "jurisdicao": fonte["jurisdicao"],
                    "localidade": None,
                    "url": f"{fonte['url']}#art{a['numero'].lower().replace('-', '')}",
                    "publicado_em": fonte["publicado_em"],
                    "verificado_em": hoje,
                    "alterado_por": sorted(set(a["alteracoes"])),
                    "fonte_hash": hashlib.sha256(dados).hexdigest(),
                    "hash": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                })
        resumo.append({"id": fonte["id"], "artigos": len(unicos),
                       "trechos": sum(1 for t in trechos if t["id"].startswith(fonte["id"] + ":"))})
        print(f"  {fonte['id']}: {len(unicos)} artigos")

    CORPUS.mkdir(parents=True, exist_ok=True)
    with (CORPUS / "trechos.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for t in trechos:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    versao = hashlib.sha256("".join(t["hash"] for t in trechos).encode()).hexdigest()[:12]
    manifesto = {"versao": versao, "gerado_em": hoje, "total_trechos": len(trechos), "fontes": resumo}
    (CORPUS / "manifesto.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Corpus {versao}: {len(trechos)} trechos em {CORPUS / 'trechos.jsonl'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baixar", action="store_true", help="baixa todas as fontes de novo")
    gerar(parser.parse_args().baixar)
