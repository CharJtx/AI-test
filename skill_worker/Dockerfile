# skill-worker — FastAPI layer on top of the embed stack (bge-m3 + sqlite-vec).
# Reuses the Dockerfile.embed deps and adds fastapi/uvicorn/httpx.

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_ENABLE_HF_TRANSFER=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
      "torch==2.4.1" --index-url https://download.pytorch.org/whl/cu124

RUN pip3 install --no-cache-dir \
      "sentence-transformers==3.3.1" \
      "transformers==4.46.3" \
      "sqlite-vec==0.1.6" \
      "huggingface_hub==0.28.1" \
      "hf_transfer==0.1.8" \
      "numpy<2" \
      "fastapi==0.115.14" \
      "uvicorn[standard]==0.32.1" \
      "httpx==0.28.1" \
      "pydantic==2.11.9"

WORKDIR /app
COPY . /app/skill_worker

ENV SKILL_WORKER_ROOT=/data/skills \
    SKILL_VLLM_BASE=http://127.0.0.1:8099/v1

EXPOSE 8000

CMD ["uvicorn", "skill_worker.app:app", "--host", "0.0.0.0", "--port", "8000"]
