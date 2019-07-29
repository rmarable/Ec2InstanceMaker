FROM python:3.6-alpine

ENV APP_HOME=/ec2instancemaker
RUN mkdir -p $APP_HOME
WORKDIR $APP_HOME

RUN apk update -q && apk add \
  autoconf \
  automake \
  gcc \
  g++ \
  libc-dev \
  libffi-dev \
  libtool \
  linux-headers \
  make \
  openssl-dev

COPY requirements.txt ./
RUN pip3 install -r requirements.txt

ARG TERRAFORM_VERSION=0.12.3
RUN wget https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN unzip terraform_${TERRAFORM_VERSION}_linux_amd64.zip
RUN cp terraform /usr/local/bin && rm terraform_*

COPY aux_data.py create_instance_terraform_templates.yml make-instance.py ./
COPY templates templates
ENV PATH=$APP_HOME:${PATH}

ENTRYPOINT ["make-instance.py"]
