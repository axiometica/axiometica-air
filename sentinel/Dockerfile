FROM ubuntu:24.04

# Ubuntu's own repos build bpftrace for every official architecture (amd64,
# arm64, armhf, ppc64el, riscv64, s390x) — using this instead of the
# iovisor/bpftrace image (amd64-only) makes the image portable across hosts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bpftrace python3 \
 && rm -rf /var/lib/apt/lists/*

COPY sentinel_http.py /sentinel_http.py

EXPOSE 9090

ENTRYPOINT ["python3", "/sentinel_http.py"]
