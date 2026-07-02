# ── builder stage ─────────────────────────────────────────────────────
# 使用 rust:1-bookworm（追踪最新稳定 1.x）。stream-core 的传递依赖（time、icu_*、
# idna_adapter、rustls 等）会随 crates.io 更新而抬高 MSRV，固定小版本会陷入
# 追赶循环，故采用滚动稳定版。含完整 toolchain + Linux linker，无需 MSVC。
# 配置国内镜像源（rsproxy）以加速依赖拉取。
FROM rust:1-bookworm AS builder
WORKDIR /src

# 先复制 cargo 配置（镜像源）。
COPY services/rust/.cargo /src/.cargo

# 复制整个 rust workspace（platform-events 等被 stream-core 依赖）。
COPY services/rust /src/services/rust

WORKDIR /src/services/rust

# 构建 release 二进制。--release 优化体积与性能；-p stream-core 只编译该 crate
# 及其依赖（不编译 workspace 中其它 stub crate）。
RUN cargo build -p stream-core --release --bin stream-core-service

# ── runtime stage ─────────────────────────────────────────────────────
# 用 debian-slim 作为运行时镜像（async-nats/hyper 依赖 libssl/libgcc）。
FROM debian:bookworm-slim AS runtime
WORKDIR /app

# 安装运行时所需的动态库（ca-certificates 用于 NATS TLS，libgcc 用于 rust 二进制）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*

# 复制编译产物。
COPY --from=builder /src/services/rust/target/release/stream-core-service /app/stream-core-service

# 暴露 HTTP 端口（默认 8101）。
EXPOSE 8101

# 运行 stream-core-service。
# 环境变量由 docker-compose 注入（NATS_URL / STREAM_CORE_ADDR 等）。
ENV STREAM_CORE_ADDR=0.0.0.0:8101
ENV RUST_LOG=info,stream_core=info
CMD ["/app/stream-core-service"]
