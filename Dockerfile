FROM python:3.11-slim-bookworm AS builder

# Use BuildKit cache for apt
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update \
    && apt-get install -y --no-install-recommends equivs gcc python3-dev

# Build dummy packages to skip installing them and their dependencies
WORKDIR /dummy-pkgs
RUN equivs-control libgl1-mesa-dri \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: libgl1-mesa-dri\nVersion: 99.0.0\nDescription: Dummy package for libgl1-mesa-dri\n' >> libgl1-mesa-dri \
    && equivs-build libgl1-mesa-dri \
    && equivs-control adwaita-icon-theme \
    && printf 'Section: misc\nPriority: optional\nStandards-Version: 3.9.2\nPackage: adwaita-icon-theme\nVersion: 99.0.0\nDescription: Dummy package for adwaita-icon-theme\n' >> adwaita-icon-theme \
    && equivs-build adwaita-icon-theme

# Copy requirements.txt first for better caching
WORKDIR /app
COPY requirements.txt .

# Use BuildKit cache for pip
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim-bookworm

# Copy installed Python packages and dummy packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /dummy-pkgs/*.deb /tmp/

# Install dependencies and create flaresolverr user
# You can test Chromium running this command inside the container:
#    xvfb-run -s "-screen 0 1600x1200x24" chromium --no-sandbox
# The error traces is like this: "*** stack smashing detected ***: terminated"
# To check the package versions available you can use this command:
#    apt-cache madison chromium
WORKDIR /app
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    # Install dummy packages
    dpkg -i /tmp/*.deb \
    # Install dependencies
    && apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-common chromium-driver xvfb dumb-init \
       procps curl vim xauth \
    # Remove temporary files and hardware decoding libraries
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/x86_64-linux-gnu/libmfxhw* \
    && rm -f /usr/lib/x86_64-linux-gnu/mfx/* \
    # Create flaresolverr user
    && useradd --home-dir /app --shell /bin/sh flaresolverr \
    && mv /usr/bin/chromedriver chromedriver \
    && chown -R flaresolverr:flaresolverr .

# Install Python dependencies
# COPY requirements.txt .
# RUN pip install -r requirements.txt \
#     # Remove temporary files
#     && rm -rf /root/.cache

USER flaresolverr

RUN mkdir -p "/app/.config/chromium/Crash Reports/pending"

COPY src .
COPY package.json ../

EXPOSE 8191
EXPOSE 8192

# dumb-init avoids zombie chromium processes
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

CMD ["/usr/local/bin/python", "-u", "/app/flaresolverr.py"]

# Local build
# docker build -t ngosang/flaresolverr:3.4.0 .
# docker run -p 8191:8191 ngosang/flaresolverr:3.4.0

# Multi-arch build
# docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
# docker buildx create --use
# docker buildx build -t ngosang/flaresolverr:3.4.0 --platform linux/386,linux/amd64,linux/arm/v7,linux/arm64/v8 .
#   add --push to publish in DockerHub

# Test multi-arch build
# docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
# docker buildx create --use
# docker buildx build -t ngosang/flaresolverr:3.4.0 --platform linux/arm/v7 --load .
# docker run -p 8191:8191 --platform linux/arm/v7 ngosang/flaresolverr:3.4.0
