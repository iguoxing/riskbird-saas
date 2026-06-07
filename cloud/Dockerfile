FROM python:3.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates fonts-liberation \
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
    libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 \
    libnss3 libu2f-udev libvulkan1 libxcomposite1 \
    libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 \
    xdg-utils libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    websockets==12.0 \
    pandas==1.5.3 \
    openpyxl==3.1.2 \
    playwright==1.40.0 \
    python-multipart==0.0.6 \
    aiofiles==23.2.1

RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

WORKDIR /app
COPY cloud/app_cloud.py /app/
COPY scraper_core.py /app/
COPY cloud/static /app/static
RUN mkdir -p /app/data/uploads /app/data/results /app/data/cookies

ENV COOKIE_FILE=/app/data/cookies/cookie.json
ENV UPLOAD_DIR=/app/data/uploads
ENV RESULT_DIR=/app/data/results

EXPOSE 8000
CMD ["uvicorn", "app_cloud:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "websockets"]
