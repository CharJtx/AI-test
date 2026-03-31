FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo-dev zlib1g-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV APP_DATA_DIR=/appdata/data
ENV APP_SCENES_DIR=/appdata/scenes

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
