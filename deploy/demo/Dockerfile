# Build the demo libFuzzer target inside Docker (clang + ASan + libFuzzer).
# Usage:
#   docker build -t clusterfuzz-demo-target -f deploy/demo-target/Dockerfile deploy/demo-target
#   docker run --rm -v "$PWD/deploy/demo-target/out:/out" clusterfuzz-demo-target
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      clang \
      llvm \
      libclang-rt-18-dev \
      zip \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY demo_fuzz.cc build.sh ./
RUN chmod +x build.sh

# Default: build revision from timestamp; override with docker run ... <revision>
ENV OUT_DIR=/out
ENV CXX=clang++
VOLUME ["/out"]

ENTRYPOINT ["./build.sh"]
