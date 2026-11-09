"""Dados compartilhados pelas buscas do RAG.

O JSONL continua sendo a fonte jurídica. O catálogo ao lado apenas acrescenta
expressões de busca e descrições temáticas, usadas para recuperar melhor o trecho.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def carregar_trechos(raiz: Path) -> list[dict]:
    corpus = raiz / "corpus"
    trechos = [json.loads(linha) for linha in (corpus / "trechos.jsonl").read_text(
        encoding="utf-8").splitlines()]
    catalogo = json.loads((corpus / "retrieval-metadata.v1.json").read_text(encoding="utf-8"))
    versao = json.loads((corpus / "manifesto.json").read_text(encoding="utf-8"))["versao"]
    anotacoes = catalogo.get("chunks", {})
    if catalogo.get("corpus_version") != versao or set(anotacoes) != {t["id"] for t in trechos}:
        raise ValueError("O catálogo de recuperação não corresponde ao corpus.")

    resultado = []
    for trecho in trechos:
        anotacao = anotacoes[trecho["id"]]
        expressoes = anotacao.get("expressions", [])
        descricoes = anotacao.get("dense_descriptions", [])
        if not expressoes or not 1 <= len(descricoes) <= 3:
            raise ValueError(f"Metadados incompletos para {trecho['id']}.")
        resultado.append({**trecho, "expressions": expressoes,
                          "dense_descriptions": descricoes})
    return resultado


def versao_indice(raiz: Path) -> str:
    """Muda quando corpus ou anotações mudam e força reindexação."""
    corpus = raiz / "corpus"
    versao = json.loads((corpus / "manifesto.json").read_text(encoding="utf-8"))["versao"]
    digest = hashlib.sha256((corpus / "retrieval-metadata.v1.json").read_bytes()).hexdigest()[:12]
    return f"{versao}:{digest}"


def validar_catalogo(raiz: Path, texto: str) -> list[str]:
    """Validação curta usada pelo editor antes de gravar o catálogo inteiro."""
    try:
        catalogo = json.loads(texto)
        ids = {t["id"] for t in carregar_trechos_sem_anotacoes(raiz)}
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        return [f"JSON ou corpus inválido: {exc}"]
    erros: list[str] = []
    if catalogo.get("corpus_version") != json.loads((raiz / "corpus" / "manifesto.json").read_text(encoding="utf-8"))["versao"]:
        erros.append("A versão do catálogo não corresponde ao manifesto do corpus.")
    anotacoes = catalogo.get("chunks")
    if not isinstance(anotacoes, dict) or set(anotacoes) != ids:
        erros.append("O catálogo deve conter exatamente uma anotação por trecho.")
        return erros
    for chunk_id, item in anotacoes.items():
        expressoes = item.get("expressions", []) if isinstance(item, dict) else []
        descricoes = item.get("dense_descriptions", []) if isinstance(item, dict) else []
        normalizadas = [" ".join(str(x).casefold().split()) for x in expressoes]
        if not expressoes or any(not x for x in normalizadas) or len(set(normalizadas)) != len(normalizadas):
            erros.append(f"{chunk_id}: expressões vazias ou repetidas.")
        if not 1 <= len(descricoes) <= 3:
            erros.append(f"{chunk_id}: informe de uma a três descrições.")
        for descricao in descricoes:
            if not isinstance(descricao, dict) or not str(descricao.get("topic", "")).strip() or not str(descricao.get("text", "")).strip():
                erros.append(f"{chunk_id}: cada descrição precisa de tema e texto.")
        temas = [" ".join(str(d.get("topic", "")).casefold().split())
                 for d in descricoes if isinstance(d, dict)]
        if len(temas) != len(set(temas)):
            erros.append(f"{chunk_id}: temas de descrição repetidos.")
    return erros


def carregar_trechos_sem_anotacoes(raiz: Path) -> list[dict]:
    return [json.loads(linha) for linha in (raiz / "corpus" / "trechos.jsonl").read_text(
        encoding="utf-8").splitlines()]
