# JobPilot AI - Kotlin sandbox image
#
# No official slim image bundles kotlinc -- this downloads the
# compiler once, at build time (with network access), so execution
# time (docker_sandbox_runner.py, --network none) never needs the
# internet at all.
FROM eclipse-temurin:17-jdk-alpine
RUN apk add --no-cache bash curl unzip \
    && curl -fsSL -o /tmp/kotlinc.zip \
       https://github.com/JetBrains/kotlin/releases/download/v1.9.24/kotlin-compiler-1.9.24.zip \
    && unzip -q /tmp/kotlinc.zip -d /opt \
    && rm /tmp/kotlinc.zip
ENV PATH="/opt/kotlinc/bin:${PATH}"
