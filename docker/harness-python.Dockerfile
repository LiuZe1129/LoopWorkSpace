# Agent-Harness Python Sandbox 基础镜像，位于单 Attempt 隔离执行环境的构建环节。
# 发布环境应以 HARNESS_DOCKER_IMAGE=<name>@sha256:<digest> 启动并开启摘要强制校验。
FROM python:3.12.7-alpine3.20

# Harness 工具只需要标准库；固定非 root 用户，避免 Agent 取得容器内管理权限。
RUN addgroup -S harness && adduser -S -G harness -u 10001 harness
USER harness
WORKDIR /workspace
