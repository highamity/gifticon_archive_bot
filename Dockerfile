FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY storage.py bot.py ./

ENV PYTHONUNBUFFERED=1
ENV GIFTICON_DB_PATH=/data/gifticons.db

CMD ["python", "bot.py"]
