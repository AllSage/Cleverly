FROM cleverly:local

USER root

ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_MODELS=/root/.ollama/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://ollama.com/install.sh | OLLAMA_NO_START=1 sh

EXPOSE 11434

ENTRYPOINT ["ollama"]
CMD ["serve"]
