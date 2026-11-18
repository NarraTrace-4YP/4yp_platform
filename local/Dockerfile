# Local-only Windmill provisioner. It is behind Compose's local-tools profile and is
# never built or run by server/update.sh.
FROM node:22-alpine

RUN apk add --no-cache bash curl jq \
    && npm install --global --no-audit --no-fund windmill-cli@1.875.0

COPY --chmod=755 windmill-bootstrap.sh /usr/local/bin/windmill-bootstrap

USER node
ENV HOME=/tmp
ENTRYPOINT ["windmill-bootstrap"]
