# NOTE on non-root: nginx's master process must start as root to bind to
# ports 80/443 (privileged ports < 1024).  nginx itself drops privileges for
# its worker processes — they run as the 'nginx' user defined inside the image.
# Adding a USER directive here would break port binding.  The safe posture is
# to ensure /etc/nginx/conf.d and /usr/share/nginx/html are owned by nginx:
FROM nginx:alpine

# Install openssl for self-signed certificate generation
RUN apk add --no-cache openssl

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy entrypoint script that generates self-signed TLS cert on first start
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ensure nginx worker processes own the content directory
RUN chown -R nginx:nginx /usr/share/nginx/html /etc/nginx/conf.d

EXPOSE 80 443

ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
