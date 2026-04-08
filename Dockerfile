FROM --platform=$BUILDPLATFORM python:3.11-slim

USER root

RUN apt-get update && \
    apt-get install --no-install-recommends -y \
        gfortran \
        make \
        git && \
    rm -rf /var/lib/apt/lists/*

# install isofit from source on the specific branch
WORKDIR /root
RUN git clone https://github.com/evan-greenbrg/isofit.git

WORKDIR /root/isofit
RUN git checkout utils/make_config && \
    git pull origin utils/make_config

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

# # Sync latest app code from S3 before running — avoids Docker rebuild for code changes
# CMD ["bash", "-c", "aws s3 sync s3://vswir-plants-config/isofit-app/ /root/app/ --region us-west-2 && python3 /root/app/entrypoint.py"]
