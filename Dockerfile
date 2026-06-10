# Build stage: install all Python deps then strip inference-irrelevant packages.
# Using a venv so the runtime stage snapshots only the final cleaned-up filesystem.
FROM python:3.12-slim AS builder

# GPU support:
#   NVIDIA (default, Turing+ incl. Blackwell):       set CUDA=true
#   NVIDIA legacy (Maxwell/Pascal/Volta, no Blackwell): set CUDA=true and CUDA_LEGACY=true
#   AMD:    set ROCM=true  (requires ROCm drivers on the host)
#   None:   leave all false (CPU-only, slow but works)
ARG CUDA=false
ARG CUDA_LEGACY=false
ARG ROCM=false

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install torch first so it gets its own cached layer.
# cu128 wheels (default) drop sm_50/60/70 to fit PyPI size limits; cu126 wheels keep
# Maxwell through Hopper but lack Blackwell (sm_100/120). See pytorch/pytorch#145544.
RUN if [ "$CUDA" = "true" ] && [ "$CUDA_LEGACY" = "true" ]; then \
      pip install --no-cache-dir \
        torch==2.7.0+cu126 \
        torchvision==0.22.0+cu126 \
        --extra-index-url https://download.pytorch.org/whl/cu126; \
    elif [ "$CUDA" = "true" ]; then \
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

# Set cache directories to /data to support read-only root FS
ENV TORCH_HOME=/data/.cache/torch \
    HF_HOME=/data/.cache/huggingface \
    XDG_CACHE_HOME=/data/.cache \
    ULTRALYTICS_CONFIG_DIR=/data/.ultralytics

# Copy code to /app
WORKDIR /app
COPY VERSION .
COPY app/ .

# Use /data as the working directory so downloads (like YOLO models) go there
WORKDIR /data
VOLUME ["/data"]

EXPOSE 8000

CMD ["python", "/app/main.py"]
