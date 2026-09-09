FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY storage.py bot.py ./

ENV PYTHONUNBUFFERED=1
ENV GIFTICON_DB_PATH=/data/gifticons.db

CMD ["python", "bot.py"]
