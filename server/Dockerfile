FROM python:3.10-slim

WORKDIR /src

COPY requirements.txt ./requirements.txt

RUN pip install -r requirements.txt

RUN apt-get update && apt-get install -y iproute2 vim dnsutils

COPY ./src/ ./src