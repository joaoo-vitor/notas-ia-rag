"""Servidor local do editor de gabarito.

Serve a página e lê/grava os arquivos do repositório do RAG. Só usa a biblioteca
padrão do Python: não precisa instalar nada.

Uso:
    python editor/servidor.py                   # usa o repositório onde o editor está
    python editor/servidor.py --rag C:/caminho/para/rag
"""

import argparse
import json
import shutil
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PASTA_APP = Path(__file__).resolve().parent
ESTATICOS = {"/": "index.html", "/app.js": "app.js", "/estilo.css": "estilo.css"}
TIPOS = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}


def criar_handler(rag):
    trechos = rag / "corpus" / "trechos.jsonl"
    gabarito = rag / "avaliacao" / "gabarito.yaml"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # sem log de cada requisição no terminal
            pass

        def responder(self, codigo, corpo, tipo="application/json"):
            dados = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
            self.send_response(codigo)
            self.send_header("Content-Type", f"{tipo}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(dados)

        def do_GET(self):
            if self.path in ESTATICOS:
                arquivo = PASTA_APP / ESTATICOS[self.path]
                return self.responder(200, arquivo.read_bytes(), TIPOS[arquivo.suffix])
            if self.path == "/api/dados":
                return self.responder(200, json.dumps({
                    "trechos": trechos.read_text(encoding="utf-8"),
                    "gabarito": gabarito.read_text(encoding="utf-8"),
                    "versao": str(gabarito.stat().st_mtime_ns),
                }))
            self.responder(404, '{"erro": "não encontrado"}')

        def do_PUT(self):
            if self.path != "/api/gabarito":
                return self.responder(404, '{"erro": "não encontrado"}')
            pedido = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            # versão como texto: o número em nanossegundos não cabe num número do JavaScript.
            # se o arquivo mudou por fora (git pull, outro editor), não sobrescreve
            if pedido["versao"] != str(gabarito.stat().st_mtime_ns):
                return self.responder(409, '{"erro": "o gabarito foi alterado fora do editor"}')
            shutil.copyfile(gabarito, gabarito.with_suffix(".yaml.bak"))
            gabarito.write_text(pedido["texto"], encoding="utf-8", newline="\n")
            self.responder(200, json.dumps({"versao": str(gabarito.stat().st_mtime_ns)}))

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rag", default=str(PASTA_APP.parent))  # editor/ fica dentro do repositório do RAG
    parser.add_argument("--porta", type=int, default=8765)
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o navegador sozinho")
    args = parser.parse_args()

    rag = Path(args.rag).resolve()
    for necessario in (rag / "corpus" / "trechos.jsonl", rag / "avaliacao" / "gabarito.yaml"):
        if not necessario.exists():
            sys.exit(f"Não encontrei {necessario}. Indique a pasta do RAG com --rag.")

    servidor = ThreadingHTTPServer(("127.0.0.1", args.porta), criar_handler(rag))
    url = f"http://127.0.0.1:{args.porta}"
    print(f"Editor do gabarito em {url}  (RAG: {rag})")
    print("Ctrl+C para encerrar.")
    if not args.sem_navegador:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
