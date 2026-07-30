# Shelf Roulette - static web tier / API reverse proxy.
FROM nginx:1.27-alpine

# Drop the stock landing/error pages before the site is copied in, so that
# anything site/ ships always wins and no base-image leftovers stay reachable.
RUN rm -f /usr/share/nginx/html/index.html /usr/share/nginx/html/50x.html

# Server config first so that editing the site does not invalidate this layer.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/

# Run unprivileged as the stock `nginx` user (uid 101).
# - the pid path must live somewhere writable, and `pid` is a main-context
#   directive so it is patched into nginx.conf here rather than into the
#   conf.d server block
# - the `user` directive is meaningless once the master is not root and only
#   produces a startup warning, so it is dropped
# - listening on 8080 instead of 80 means no privileged port is needed
RUN set -eux; \
    sed -i 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf; \
    sed -i '/^user  *nginx;/d' /etc/nginx/nginx.conf; \
    mkdir -p /var/cache/nginx; \
    chown -R nginx:nginx /var/cache/nginx /run /etc/nginx/conf.d; \
    chown -R nginx:nginx /usr/share/nginx/html; \
    nginx -t; \
    # `nginx -t` runs as root and leaves an empty root-owned /tmp/nginx.pid
    # behind. /tmp is sticky (1777), so uid 101 could never overwrite it and
    # startup would die with open() "/tmp/nginx.pid" failed (13). Drop it.
    rm -f /tmp/nginx.pid

USER nginx

EXPOSE 8080

# curl and wget are both present in nginx:1.27-alpine; wget is the lighter call.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
