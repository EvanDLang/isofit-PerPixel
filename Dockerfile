FROM --platform=$BUILDPLATFORM python:3.11-slim

USER root

RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gfortran \
        make \
        git && \
    rm -rf /var/lib/apt/lists/*

# install isofit from source from dev
WORKDIR /root
RUN git clone --branch v4.0.0-beta https://github.com/isofit/isofit.git

WORKDIR /root/isofit

WORKDIR /root

RUN pip install --no-cache-dir -e "isofit[docker]" && \
    isofit -b . download all && \
    isofit build

RUN pip install --no-cache-dir "ray[client]"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir awscli

# copy application code — will be overwritten by S3 sync at runtime
COPY app/ /root/app/

ENV PYTHONUNBUFFERED=1

WORKDIR /root/app

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
