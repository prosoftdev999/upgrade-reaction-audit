FROM python:3.13-slim-bookworm

WORKDIR /app

COPY data/ /evidence/
COPY entrypoint.py /usr/local/bin/task-entrypoint.py

RUN mkdir -p /home/agent /app/output \
    && ln -s /evidence /app/data \
    && chown -R 1000:1000 /home/agent /app/output \
    && chmod 0755 /usr/local/bin/task-entrypoint.py

ENV HOME=/home/agent

ENTRYPOINT ["python3", "/usr/local/bin/task-entrypoint.py"]
CMD ["sleep", "infinity"]
