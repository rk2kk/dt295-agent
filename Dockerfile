# SPDX-License-Identifier: GPL-2.0-or-later
FROM debian:bookworm-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*

COPY dt295-recover.c /src/dt295-recover.c
RUN cc -O2 -Wall -Wextra -o /src/dt295-recover /src/dt295-recover.c -lusb-1.0

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 libusb-1.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/pix
COPY pix-ts-legacy /opt/pix/pix-ts-legacy
COPY assets/ /opt/pix/assets/
COPY --from=build /src/dt295-recover /opt/dt295-recover
RUN chmod +x /opt/dt295-recover
COPY dt295-agent.py /opt/dt295-agent.py

EXPOSE 25252
ENTRYPOINT ["python3", "/opt/dt295-agent.py"]
