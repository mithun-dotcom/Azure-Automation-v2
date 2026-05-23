# Render runs this container. It carries both the Python backend and PowerShell 7 +
# the ExchangeOnlineManagement module for the two Exchange-only operations.
FROM python:3.12-slim

# --- Install PowerShell 7 ---------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget apt-transport-https software-properties-common ca-certificates gnupg \
    && wget -q "https://github.com/PowerShell/PowerShell/releases/download/v7.4.6/powershell_7.4.6-1.deb_amd64.deb" \
        -O /tmp/powershell.deb \
    && dpkg -i /tmp/powershell.deb || apt-get install -f -y \
    && rm /tmp/powershell.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- Install the Exchange Online module -------------------------------------
RUN pwsh -NoProfile -Command \
    "Set-PSRepository -Name PSGallery -InstallationPolicy Trusted; \
     Install-Module -Name ExchangeOnlineManagement -RequiredVersion 3.7.0 -Scope AllUsers -Force"

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY powershell ./powershell

# Render provides $PORT
ENV PORT=8000
CMD ["sh", "-c", "uvicorn backend:app --host 0.0.0.0 --port ${PORT}"]
