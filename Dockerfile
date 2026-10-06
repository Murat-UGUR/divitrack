FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')" || exit 1
# Veriler host'ta kalsın:
#   docker run -p 127.0.0.1:8501:8501 -v "$(pwd)/data:/app/data" divitrack
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0"]
