FROM quay.io/iovisor/bpftrace:latest

# bpftrace image is Ubuntu-based; add Python3 for the HTTP wrapper.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

COPY sentinel_http.py /sentinel_http.py

EXPOSE 9090

ENTRYPOINT ["python3", "/sentinel_http.py"]
