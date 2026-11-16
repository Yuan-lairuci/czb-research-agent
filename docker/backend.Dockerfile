# 后端镜像：FastAPI + uvicorn（Python 3.11）
# 构建参数（国内网络加速，评委默认官方源即可）：
#   REGISTRY=镜像仓库前缀，如 docker.1ms.run/
#   PIP_INDEX=PyPI 镜像，如 https://mirrors.aliyun.com/pypi/simple/
ARG REGISTRY=
FROM ${REGISTRY}python:3.11-slim

WORKDIR /srv/app

ARG PIP_INDEX=https://pypi.org/simple

COPY backend/requirements*.txt ./
RUN pip install --no-cache-dir -i ${PIP_INDEX} $(for f in requirements*.txt; do echo -r "$f"; done)

COPY backend/ ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
