# 后端镜像：FastAPI + uvicorn
FROM python:3.11-slim

WORKDIR /srv/app

COPY backend/requirements*.txt ./
RUN pip install --no-cache-dir $(for f in requirements*.txt; do echo -r "$f"; done)

COPY backend/ ./

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
