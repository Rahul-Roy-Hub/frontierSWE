FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm

RUN useradd --create-home --shell /bin/bash agent

WORKDIR /app

COPY spec.md /app/spec.md
COPY selfcheck.py /app/selfcheck.py
COPY pagestore.py /app/pagestore.py
COPY public/ /app/public/

RUN chown -R agent:agent /app
