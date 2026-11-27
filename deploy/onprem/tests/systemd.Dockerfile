ARG BASE
FROM ${BASE}
ARG BASE
RUN if [ "${BASE}" = almalinux:9 ]; then \
      dnf install -y systemd python3.11 python3.11-pip curl iproute iptables util-linux shadow-utils && dnf clean all; \
    else \
      apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y systemd systemd-sysv curl iproute2 iptables util-linux passwd ca-certificates && \
      if [ "${BASE}" = ubuntu:22.04 ]; then apt-get install -y python3.11 python3.11-venv; else apt-get install -y python3 python3-venv; fi && \
      rm -rf /var/lib/apt/lists/*; \
    fi
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
