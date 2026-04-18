FROM node:20-slim

WORKDIR /app

COPY package*.json ./

RUN npm ci

COPY . .

EXPOSE 3000

CMD ["node", "node_modules/vite/bin/vite.js", "--host", "0.0.0.0", "--port", "3000"]
