FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# GPU support:
#   NVIDIA: set CUDA=true
#   AMD:    set ROCM=true  (requires ROCm drivers on the host)
#   None:   leave both false (CPU-only, slow but works)
ARG CUDA=false
ARG ROCM=false
RUN if [ "$CUDA" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.7.0+cu128 \
        torchvision==0.22.0+cu128 \
        --extra-index-url https://download.pytorch.org/whl/cu128; \
    elif [ "$ROCM" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.7.0 \
        torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/rocm6.2; \
    else \
      pip install --no-cache-dir torch==2.7.0 torchvision==0.22.0; \
    fi

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --force-reinstall opencv-python-headless

COPY app/ .

# /data is the mounted volume: pets/luna/, pets/config.json, state files, logs
VOLUME ["/data"]

EXPOSE 8000

CMD ["python", "main.py"]
