FROM python:3.12-slim
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-services.txt
COPY . .
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/var && chown appuser /app/var
USER appuser
EXPOSE 8000
CMD ["uvicorn", "api.index:app", "--host", "0.0.0.0", "--port", "8000"]
