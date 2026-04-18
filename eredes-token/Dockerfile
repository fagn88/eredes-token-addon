ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    chromium \
    chromium-chromedriver \
    xvfb \
    x11vnc \
    ttf-freefont \
    font-noto \
    bash \
    git

# Install noVNC manually
RUN git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc && \
    git clone --depth 1 https://github.com/novnc/websockify.git /opt/novnc/utils/websockify && \
    ln -s /opt/novnc/vnc.html /opt/novnc/index.html

# Install Python packages
RUN pip3 install --no-cache-dir --break-system-packages selenium requests

# Set up directories
RUN mkdir -p /data/chrome-profile /app

# Copy application files
COPY eredes_token.py /app/
COPY rootfs /
