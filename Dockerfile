# Servidor MCP jurídico do Notas.ia (python -m legal_rag serve).
#
# O corpus vai dentro da imagem e os índices são gerados na construção, então o índice
# sempre corresponde ao corpus da imagem. O modelo de embeddings também é baixado aqui
# (BGE-M3, ~2,3 GB): o container não acessa o Hugging Face ao subir.
#
#   docker build -t notas-ia-legal-mcp .
#   docker build -t notas-ia-legal-mcp --build-arg LEGAL_STRATEGY=bm25 --build-arg EMBEDDING_MODEL= .
#   docker run -p 127.0.0.1:8001:8001 notas-ia-legal-mcp

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface

WORKDIR /app

# PyTorch só de CPU (bem menor que o padrão, que traz CUDA)
COPY requirements.txt ./
RUN python -m pip install "torch==$(grep -oP '^torch==\K.*' requirements.txt)" \
        --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install -r requirements.txt

# camada separada: mudar o código não baixa o modelo de novo
ARG EMBEDDING_MODEL=BAAI/bge-m3
RUN if [ -n "$EMBEDDING_MODEL" ]; then \
        python -c "import sys; from sentence_transformers import SentenceTransformer; SentenceTransformer(sys.argv[1], device='cpu')" "$EMBEDDING_MODEL"; \
    fi

COPY corpus/ corpus/
COPY legal_rag/ legal_rag/
COPY busca_lexical.py busca_densa.py ./

ARG LEGAL_STRATEGY=hibrida-bge-m3
RUN python -m legal_rag index --strategy "$LEGAL_STRATEGY"

ENV HF_HUB_OFFLINE=1

EXPOSE 8001

CMD ["python", "-m", "legal_rag", "serve", "--host", "0.0.0.0", "--port", "8001"]
