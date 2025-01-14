FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
COPY Valley4Server_re.py bot.py
RUN apt-get -y update
RUN apt-get install -y ffmpeg
RUN pip install PyNaCl
RUN pip install -r requirements.txt
CMD ["python", "bot.py"]