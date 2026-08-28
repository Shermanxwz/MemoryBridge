FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 --create-home memorybridge && mkdir -p /var/lib/memorybridge && chown -R memorybridge:memorybridge /var/lib/memorybridge /app
USER memorybridge
EXPOSE 8765
CMD ["memorybridge-server"]
