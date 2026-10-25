FROM python:3.12-alpine
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY server/storage.py server/app.py ./
RUN addgroup -S -g 10001 capture && adduser -S -D -u 10001 -G capture capture && mkdir -p /data && chown capture:capture /data
USER capture
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health',timeout=2).read()"
CMD ["python","app.py","--host","0.0.0.0","--port","8787"]
