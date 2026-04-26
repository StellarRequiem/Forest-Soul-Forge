# Frontend static serve — nginx:alpine.
#
# Why nginx and not python -m http.server (which is what run.command uses
# for local dev)?
#
#   * http.server is single-threaded and has no gzip, no cache-control, no
#     access log format you'd want to parse. Fine for a dev loop, wrong for
#     a container the user might actually run beyond the first boot.
#   * nginx:alpine is ~25MB and has been the default "serve static files in
#     a container" baseline for a decade. Explicit nginx.conf documents
#     MIME mapping and caching policy.
#   * ES modules — the frontend uses import statements — require the server
#     to send Content-Type: application/javascript for .js files. nginx does
#     that out of the box; http.server's default MIME for .js was buggy on
#     old Pythons and is not worth trusting in production.
#
# Build context is the frontend/ dir itself, NOT the repo root, so the
# image stays tiny and unrelated source can't sneak in. See
# docker-compose.yml — the service sets `context: ./frontend`.

FROM nginx:1.27-alpine

# Replace the default server block with one that serves /usr/share/nginx/html
# and correctly handles the query-string API= param by not trying to route
# it. Index fallback keeps deep links working if we ever add client routing.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Static assets.
COPY css /usr/share/nginx/html/css
COPY js  /usr/share/nginx/html/js
COPY index.html /usr/share/nginx/html/index.html

# Make assets world-readable (a+rX = read for files, read+execute for dirs).
# COPY preserves host file modes, and on macOS the home dir tree often has
# mode 600/700, which leaves nginx (running as the unprivileged nginx user)
# unable to open the files — symptom is a 403 with `Permission denied`
# in nginx's error log. Same chmod we apply on the daemon image.
RUN chmod -R a+rX /usr/share/nginx/html

# nginx:alpine already exposes 80 and runs as non-root via the nginx user.
# We expose 5173 conceptually via the compose port mapping; inside the
# container nginx still listens on 80. Keeping this layer minimal.
EXPOSE 80

# HEALTHCHECK: any 2xx from / proves nginx is up and has the files.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
