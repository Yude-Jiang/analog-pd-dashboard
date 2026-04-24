FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements_cloudrun.txt .
RUN pip install --no-cache-dir -r requirements_cloudrun.txt

# Copy application files
COPY app.py .
COPY dashboard.html .
COPY data.json .
COPY yjbb_annual.json .
COPY profiles_xq.json .
COPY fetch_yjbb_annual.py .
COPY fetch_yjbb_quarterly.py .
COPY yjbb_quarterly.json .
COPY fetch_profiles.py .
COPY fetch_edgar_to_json.py .
COPY fetch_silergy_to_json.py .
COPY fetch_semi_data.py .
COPY sync_data.py .
COPY validate_data.py .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
