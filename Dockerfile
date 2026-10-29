# Use a Python 3.12.3 Alpine base image
FROM python:3.12-alpine3.20

# Set the working directory
WORKDIR /app

# Copy all files from the current directory to the container's /app directory
COPY . .

# Install necessary dependencies
RUN apk add --no-cache \
    gcc \
    libffi-dev \
    musl-dev \
    python3-dev \
    ffmpeg \
    aria2 \
    make \
    g++ \
    cmake \
    gcompat && \
    wget -q https://github.com/axiomatic-systems/Bento4/archive/v1.6.0-639.zip && \
    unzip v1.6.0-639.zip && \
    cd Bento4-1.6.0-639 && \
    mkdir build && \
    cd build && \
    cmake .. && \
    make -j$(nproc) && \
    cp mp4decrypt /usr/local/bin/ &&\
    cd ../.. && \
    rm -rf Bento4-1.6.0-639 v1.6.0-639.zip && \
    wget -q https://github.com/nilaoda/N_m3u8DL-RE/releases/download/v0.6.0-beta/N_m3u8DL-RE_v0.6.0-beta_linux-x64_20260629.tar.gz && \
    tar -xzf N_m3u8DL-RE_v0.6.0-beta_linux-x64_20260629.tar.gz && \
    mv N_m3u8DL-RE /usr/local/bin/ && \
    chmod +x /usr/local/bin/N_m3u8DL-RE && \
    rm -f N_m3u8DL-RE_v0.6.0-beta_linux-x64_20260629.tar.gz

# Install Python dependencies
RUN pip3 install --no-cache-dir --upgrade pip \
    && pip3 install --no-cache-dir --upgrade -r sainibots.txt \
    && python3 -m pip install -U yt-dlp

# Set the command to run the application
CMD ["sh", "-c", "gunicorn app:app & python3 modules/main.py"]

