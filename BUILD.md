```bash
docker login ghcr.io -u svaningelgem   # enter token that has at least read:repo & write_packages

docker build -t ghcr.io/svaningelgem/flaresolverr:latest .

docker push ghcr.io/svaningelgem/flaresolverr:latest

docker pull ghcr.io/svaningelgem/flaresolverr:latest

docker run -d \
  --name flaresolverr \
  --restart unless-stopped \
  -p 8191:8191 \
  -e DRIVER=nodriver \
  ghcr.io/svaningelgem/flaresolverr:latest
```