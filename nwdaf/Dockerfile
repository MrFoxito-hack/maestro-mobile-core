FROM python:3.11-slim
WORKDIR /opt/maestro/nwdaf
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tools ./tools
COPY hypercorn.toml .
RUN python tools/fetch_contract.py && useradd --uid 10001 --create-home nwdaf \
    && mkdir -p data && chown -R nwdaf:nwdaf /opt/maestro
USER nwdaf
# P.1203 is intentionally NOT included: no redistribution rights for that dependency.
# Mount data/ for persistence and configure TLS/access control before non-loopback exposure.
CMD ["python", "-m", "hypercorn", "--config", "hypercorn.toml", "app.main:app"]
