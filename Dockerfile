FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY remote_dorado/ ./remote_dorado/
RUN pip install --no-cache-dir .
RUN useradd -r -u 10001 dorado && mkdir -p /app/data && chown dorado:dorado /app/data
USER dorado
EXPOSE 8765
CMD ["remote-dorado-server"]
