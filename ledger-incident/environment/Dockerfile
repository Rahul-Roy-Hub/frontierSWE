FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm

RUN useradd --create-home --shell /bin/bash agent

WORKDIR /app

COPY server.py /app/server.py
COPY selfcheck.py /app/selfcheck.py
COPY data/ /app/data/
COPY host/ /app/host/

RUN chown -R agent:agent /app
