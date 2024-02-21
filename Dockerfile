FROM python:3.10
WORKDIR /app
COPY requirements.txt .
COPY Valley4Server_re.py bot.py
RUN pip install PyNaCl
RUN pip install -r requirements.txt
RUN apt-get -y update
RUN apt-get install -y ffmpeg
CMD ["python", "bot.py"]