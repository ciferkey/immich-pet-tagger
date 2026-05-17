# Build stage: install all Python deps then strip inference-irrelevant packages.
# Using a venv so the runtime stage snapshots only the final cleaned-up filesystem.
FROM python:3.12-slim AS builder

# GPU support:
#   NVIDIA: set CUDA=true
#   AMD:    set ROCM=true  (requires ROCm drivers on the host)
#   None:   leave both false (CPU-only, slow but works)
ARG CUDA=false
ARG ROCM=false

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install torch first so it gets its own cached layer.
RUN if [ "$CUDA" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.7.0+cu128 \
        torchvision==0.22.0+cu128 \
        --extra-index-url https://download.pytorch.org/whl/cu128; \
    elif [ "$ROCM" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.7.0 \
        torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/rocm6.3; \
    else \
      pip install --no-cache-dir torch==2.7.0 torchvision==0.22.0 \
        --index-url https://download.pytorch.org/whl/cpu; \
    fi

COPY requirements.txt .
# All nvidia-*-cu12 packages except triton are hard-required by torch at import time:
# torch.__init__.py preloads them via ctypes before loading torch._C, and libtorch_cuda.so
# has them in its NEEDED list. Triton is only used by torch.compile(), not inference.
# opencv-python (GUI variant, installed by ultralytics) is replaced by headless;
# explicit uninstall removes the orphaned opencv_python.libs directory.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless \
    && pip uninstall -y triton 2>/dev/null || true

# Runtime stage: clean base + only the final venv state (no ghost install layers).
FROM python:3.12-slim

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# /data is the mounted volume: pets/luna/, pets/config.json, state files, logs
VOLUME ["/data"]

EXPOSE 8000

COPY VERSION .
COPY app/ .

CMD ["python", "main.py"]
