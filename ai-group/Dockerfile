FROM python:3.12-slim AS source
WORKDIR /build
COPY . .
RUN python3 export_source.py /tmp/chat.zip && python3 -m zipfile -e /tmp/chat.zip /release
FROM python:3.12-slim
WORKDIR /app
COPY --from=source /release/ai-group/ /app/
RUN mkdir /data && chown 65534:65534 /data
USER 65534:65534
EXPOSE 8787
CMD ["python3", "manage.py", "start", "--data-dir", "/data", "--host", "0.0.0.0"]
