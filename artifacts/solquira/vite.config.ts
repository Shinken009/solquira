import path from 'path';
import { cpSync, createReadStream, existsSync, statSync } from 'node:fs';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

const port = Number(process.env.PORT || '5173');
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PORT debe ser un puerto TCP válido.');
}
const basePath = process.env.BASE_PATH || '/';
const cesiumBase = `${basePath.endsWith('/') ? basePath : basePath + '/'}cesium/`;
const cesiumAssets = path.resolve(import.meta.dirname, 'node_modules/cesium/Build/Cesium');
let cesiumOutput = '';

export default defineConfig({
  base: basePath,
  plugins: [
    react(),
    tailwindcss(),
    {
      name: 'solquira-local-cesium-assets',
      configResolved(config) { cesiumOutput = path.resolve(config.root, config.build.outDir, 'cesium'); },
      closeBundle() {
        for (const directory of ['Assets', 'Workers', 'ThirdParty', 'Widgets']) {
          cpSync(path.join(cesiumAssets, directory), path.join(cesiumOutput, directory), { recursive: true });
        }
        for (const notice of ['LICENSE.md', 'ThirdParty.json', 'ThirdParty.extra.json']) {
          cpSync(path.resolve(cesiumAssets, '../..', notice), path.join(cesiumOutput, notice));
        }
      },
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const pathname = (request.url || '').split('?')[0];
          if (!pathname.startsWith(cesiumBase)) return next();
          let relative: string;
          try { relative = decodeURIComponent(pathname.slice(cesiumBase.length)); } catch { response.statusCode = 400; return response.end(); }
          const filename = path.resolve(cesiumAssets, relative);
          if (!filename.startsWith(cesiumAssets + path.sep) || !existsSync(filename) || !statSync(filename).isFile()) { response.statusCode = 404; return response.end(); }
          const mime: Record<string, string> = { '.js': 'application/javascript', '.json': 'application/json', '.xml': 'application/xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml', '.wasm': 'application/wasm', '.css': 'text/css' };
          response.setHeader('Content-Type', mime[path.extname(filename)] || 'application/octet-stream');
          createReadStream(filename).pipe(response);
        });
      },
    },
  ],
  define: { CESIUM_BASE_URL: JSON.stringify(cesiumBase) },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
      '@assets': path.resolve(
        import.meta.dirname,
        '..',
        '..',
        'attached_assets',
      ),
    },
    dedupe: ['react', 'react-dom'],
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, process.env.VITE_DEMO_MODE === 'true' ? 'dist/demo' : 'dist/public'),
    emptyOutDir: true,
  },
  server: {
    port,
    strictPort: true,
    host: '127.0.0.1',
    allowedHosts: ['localhost', '127.0.0.1'],
    fs: { strict: true },
    proxy: {
      '/v1': {
        target: process.env.SOLQUIRA_BACKEND_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/solquira-api': {
        target: process.env.SOLQUIRA_BACKEND_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: process.env.SOLQUIRA_BACKEND_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port,
    host: '127.0.0.1',
    allowedHosts: ['localhost', '127.0.0.1'],
  },
});
