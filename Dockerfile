FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# CUDA=true installs GPU-enabled PyTorch; default is CPU-only
ARG CUDA=false
RUN if [ "$CUDA" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.6.0+cu126 \
        torchvision==0.21.0+cu126 \
        --extra-index-url https://download.pytorch.org/whl/cu126; \
    else \
      pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --force-reinstall opencv-python-headless

COPY app/ .

# /data is the mounted volume: pets/luna/, pets/config.json, state files, logs
VOLUME ["/data"]

EXPOSE 8000

CMD ["python", "main.py"]
