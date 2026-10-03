FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN groupadd --gid 10001 charging && useradd --uid 10001 --gid charging --no-create-home charging \
    && mkdir -p /app/data && chown 10001:10001 /app/data
USER 10001:10001
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/ready', timeout=3)"
CMD ["hypercorn", "app.main:app", "--bind", "0.0.0.0:8081", "--workers", "1"]
