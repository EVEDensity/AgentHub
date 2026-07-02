# syntax=docker/dockerfile:1.4
# ── builder stage ─────────────────────────────────────────────────────
# 使用 rust:1-bookworm（追踪最新稳定 1.x）。fanout-core 的传递依赖
# （async-nats、hyper、tokio、icu_* 等）会随 crates.io 更新而抬高 MSRV，
# 固定小版本会陷入追赶循环，故采用滚动稳定版。
# 配置国内镜像源（rsproxy）以加速依赖拉取。
#
# BuildKit 缓存挂载说明：
# - /usr/local/cargo/registry：已下载的 .crate 包。即使源码层 COPY 失效，
#   已下载的 crate 仍保留在 BuildKit 缓存中，避免重新从 rsproxy.cn 拉取
#   （rsproxy.cn 偶发 SSL/TLS 握手失败）。
# - /usr/local/cargo/git：git 依赖。
# - /cargo-target：编译产物。把 CARGO_TARGET_DIR 重定向到此处，源码改动
#   不会让编译缓存失效，cargo 会增量编译。
FROM rust:1-bookworm AS builder
WORKDIR /src

# 先复制 cargo 配置（镜像源）。
COPY services/rust/.cargo /src/.cargo

# 复制整个 rust workspace（platform-events 等被 fanout-core 依赖）。
COPY services/rust /src/services/rust

WORKDIR /src/services/rust

# 把编译产物重定向到缓存目录，避免与源码层耦合。
ENV CARGO_TARGET_DIR=/cargo-target

# 构建 release 二进制。--release 优化体积与性能；-p fanout-core 只编译该
# crate 及其依赖（不编译 workspace 中其它 stub crate）。
# cache 挂载持久化 cargo registry / git / target，跨构建复用。
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,target=/cargo-target,sharing=locked \
    cargo build -p fanout-core --release --bin fanout-core-service \
    && cp /cargo-target/release/fanout-core-service /tmp/fanout-core-service

# ── runtime stage ─────────────────────────────────────────────────────
# 用 debian-slim 作为运行时镜像（hyper 依赖 libssl/libgcc）。
FROM debian:bookworm-slim AS runtime
WORKDIR /app

# 安装运行时所需的动态库（ca-certificates 用于 HTTPS TLS，libgcc-s1 用于 rust 二进制）。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgcc-s1 \
    && rm -rf /var/lib/apt/lists/*

# 复制编译产物（从 /tmp 取，因为 CARGO_TARGET_DIR 在缓存挂载里，COPY --from 不能直接引用缓存挂载路径）。
COPY --from=builder /tmp/fanout-core-service /app/fanout-core-service

# 暴露 HTTP 端口（默认 8103）。
EXPOSE 8103

# 运行 fanout-core-service。
# 环境变量由 docker-compose 注入（NATS_URL / FANOUT_CORE_ADDR / FC_* 等）。
ENV FANOUT_CORE_ADDR=0.0.0.0:8103
ENV RUST_LOG=info,fanout_core=info
CMD ["/app/fanout-core-service"]
