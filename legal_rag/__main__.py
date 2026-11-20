import argparse
import sys

from legal_rag.busca import Buscador, estrategias, indexar


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m legal_rag", description=__doc__)
    comandos = parser.add_subparsers(dest="comando", required=True)

    index = comandos.add_parser("index", help="gera os índices da estratégia escolhida")
    index.add_argument("--strategy", default="hibrida-bge-m3",
                       help=f"bm25 ou uma de: {', '.join(estrategias())}")

    serve = comandos.add_parser("serve", help="sobe o servidor MCP em /mcp")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)
    serve.add_argument("--strategy", help="padrão: a do último index")

    busca = comandos.add_parser("search", help="testa a busca no terminal, sem servidor")
    busca.add_argument("query")
    busca.add_argument("--limit", type=int, default=5)
    busca.add_argument("--strategy")

    args = parser.parse_args()
    if args.comando == "index":
        indexar(args.strategy)
    elif args.comando == "search":
        buscador = Buscador(args.strategy)
        resposta = buscador.buscar(args.query, args.limit)
        print(f"{resposta['effective_strategy']} · corpus {resposta['corpus_version']}"
              + (f" · fallback: {resposta['fallback_reason']}" if resposta["fallback_reason"] else ""))
        for r in resposta["results"]:
            print(f"- {r['chunk_id']}  {r['document']}, {r['section']}")
    else:
        from legal_rag.servidor import criar_servidor
        buscador = Buscador(args.strategy)  # carrega o modelo antes de aceitar conexões
        print(f"Busca {buscador.efetiva} pronta"
              + (f" (fallback: {buscador.fallback_reason})" if buscador.fallback_reason else ""))
        criar_servidor(buscador).run(transport="http", host=args.host, port=args.port,
                                     path="/mcp", show_banner=False)


if __name__ == "__main__":
    main()
