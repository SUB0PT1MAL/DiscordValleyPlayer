#FROM python:3.10-slim
FROM python:3.10-alpine

WORKDIR /app

#RUN apt-get update && apt-get install -y ffmpeg libopus0 libffi-dev python3-dev gcc && rm -rf /var/lib/apt/lists/*
RUN apk add --no-cache ffmpeg opus-dev gcc musl-dev python3-dev libffi-dev

COPY requirements.txt .
COPY Valley4Server_re.py bot.py

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir PyNaCl

CMD ["python", "bot.py"]
