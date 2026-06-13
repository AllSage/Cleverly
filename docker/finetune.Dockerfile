ARG BASE_IMAGE=cleverly:local
FROM ${BASE_IMAGE}

USER root
COPY requirements-finetune.txt /tmp/requirements-finetune.txt
RUN pip install --no-cache-dir -r /tmp/requirements-finetune.txt \
    && rm -f /tmp/requirements-finetune.txt

