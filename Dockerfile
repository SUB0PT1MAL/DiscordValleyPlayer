FROM python:3.10
WORKDIR /app
COPY requirements.txt .
COPY Main.py bot.py
COPY cogs/ .
RUN pip install -r requirements.txt
RUN apt-get -y update
RUN apt-get install -y ffmpeg
CMD ["python", "bot.py"]