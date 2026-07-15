FROM node:22-slim AS build
WORKDIR /app
COPY apps/dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/dashboard .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
