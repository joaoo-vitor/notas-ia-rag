"""Cliente mínimo da API do Gemini (generateContent), usado pela geração de palavras-chave e
pela geração de respostas. Só biblioteca padrão; a chave vem de GEMINI_API_KEY."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
MODELO_PADRAO = "gemini-3.8-flash"


class ErroGemini(Exception):
    pass


ARQUIVO_ENV = Path(__file__).resolve().parent / ".env"  # fora do Git (.gitignore)


def chave_do_ambiente():
    """GEMINI_API_KEY do ambiente ou, se não houver, da linha GEMINI_API_KEY=... do .env."""
    chave = os.environ.get("GEMINI_API_KEY")
    if not chave and ARQUIVO_ENV.exists():
        for linha in ARQUIVO_ENV.read_text(encoding="utf-8").splitlines():
            nome, _, valor = linha.partition("=")
            if nome.strip() == "GEMINI_API_KEY":
                chave = valor.strip().strip('"').strip("'")
    if not chave:
        raise ErroGemini("Defina GEMINI_API_KEY no ambiente ou no arquivo .env deste repositório.")
    return chave


def corpo_do_pedido(texto, esquema=None, sistema=None, temperatura=None):
    """Corpo JSON do pedido. Separado da chamada para servir de chave de cache."""
    config = {}
    if temperatura is not None:
        config["temperature"] = temperatura
    if esquema is not None:
        config["responseMimeType"] = "application/json"
        config["responseSchema"] = esquema
    corpo = {"contents": [{"role": "user", "parts": [{"text": texto}]}]}
    if sistema:
        corpo["systemInstruction"] = {"parts": [{"text": sistema}]}
    if config:
        corpo["generationConfig"] = config
    return corpo


def chamar(corpo, modelo=MODELO_PADRAO, chave=None, tentativas=6, timeout=120, avisar=print):
    """Envia o pedido e devolve {"texto", "json", "uso", "modelo", "fim"}.

    Repete em limite de taxa (429) e sobrecarga (500/503), esperando cada vez mais. Outros erros
    HTTP sobem como ErroGemini na hora."""
    chave = chave or chave_do_ambiente()
    dados = json.dumps(corpo).encode("utf-8")
    pedido = urllib.request.Request(API.format(modelo=modelo), data=dados, method="POST", headers={
        "Content-Type": "application/json", "x-goog-api-key": chave})
    for tentativa in range(tentativas):
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                bruto = json.loads(resposta.read().decode("utf-8"))
            return _interpretar(bruto, modelo, corpo)
        except urllib.error.HTTPError as erro:
            corpo_erro = erro.read().decode("utf-8", "replace")
            if erro.code not in (429, 500, 503) or tentativa == tentativas - 1:
                raise ErroGemini(f"Erro do Gemini (HTTP {erro.code}): {corpo_erro[:800]}") from erro
            cota, sugerida = _detalhes_do_limite(corpo_erro)
            if cota and "PerDay" in cota:  # cota diária: esperar não adianta
                raise ErroGemini(f"Cota diária esgotada ({cota}); continue amanhã") from erro
            motivo = f"HTTP {erro.code}" + (f" ({cota})" if cota else "")
        except (TimeoutError, urllib.error.URLError, ConnectionError) as erro:
            # servidor sobrecarregado costuma aparecer como demora sem resposta
            if tentativa == tentativas - 1:
                raise ErroGemini(f"O Gemini não respondeu: {erro}") from erro
            motivo, sugerida = f"sem resposta ({type(erro).__name__})", None
        # limite ou sobrecarga do free tier: espera o que a API sugerir, ou cada vez mais
        espera = sugerida + 2 if sugerida else 30 * (tentativa + 1)
        avisar(f"  {time.strftime('%H:%M:%S')} {motivo}; tentando de novo em {espera:.0f} s")
        time.sleep(espera)
    raise ErroGemini("O Gemini não respondeu depois de várias tentativas.")


def _detalhes_do_limite(corpo_erro):
    """Do corpo de um 429: qual cota estourou (por minuto, por dia...) e a espera sugerida."""
    try:
        detalhes = json.loads(corpo_erro)["error"].get("details", [])
    except (ValueError, KeyError, AttributeError):
        return None, None
    cota, espera = None, None
    for d in detalhes:
        for v in d.get("violations", []):
            cota = v.get("quotaId") or v.get("quotaMetric") or cota
        if "retryDelay" in d:
            try:
                espera = float(str(d["retryDelay"]).rstrip("s"))
            except ValueError:
                pass
    return cota, espera


def _interpretar(bruto, modelo, corpo):
    candidatos = bruto.get("candidates") or []
    if not candidatos:
        motivo = (bruto.get("promptFeedback") or {}).get("blockReason", "sem candidatos")
        raise ErroGemini(f"O Gemini não devolveu resposta ({motivo})")
    candidato = candidatos[0]
    partes = (candidato.get("content") or {}).get("parts") or []
    # partes de raciocínio ("thought") não fazem parte da resposta
    texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
    resultado = {
        "texto": texto,
        "json": None,
        "uso": bruto.get("usageMetadata", {}),
        "modelo": bruto.get("modelVersion", modelo),
        "fim": candidato.get("finishReason"),
    }
    if "responseSchema" in corpo.get("generationConfig", {}):
        try:
            resultado["json"] = json.loads(texto)
        except ValueError as erro:
            raise ErroGemini(f"O Gemini não devolveu JSON válido (fim: {resultado['fim']})") from erro
    return resultado
