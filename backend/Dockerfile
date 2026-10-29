FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

# Apply latest OS security updates to clear base-image CVEs
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get -y install --no-install-recommends ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip toolchain to clear base-image Python CVEs (pip, wheel, jaraco.context)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel "jaraco.context>=6.1.0"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Remove any cached bytecode so Python uses the fresh source files
RUN find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find . -name "*.pyc" -delete 2>/dev/null || true

RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
