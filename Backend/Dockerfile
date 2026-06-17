FROM python:3.10-slim
EXPOSE 5000
ENV PYTHONDONTWRITEBYTECODE=1
ENV TIME_ZONE=Asia/Shanghai
RUN echo "${TIME_ZONE}" > /etc/timezone \
    && ln -sf /usr/share/zoneinfo/${TIME_ZONE} /etc/localtime
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=development
ENV FLASK_APP=run.py
ENV DEBUG=True
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN python -m pip install --no-cache-dir -r requirements.txt
WORKDIR /app
# COPY env.sample .env
COPY . /app
RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser
VOLUME [ "/data" ]
CMD ["python", "app.py"]
