import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {resolve} from 'node:path';

export default defineConfig({
  base: '/editor/',
  plugins: [react()],
  build: {
    outDir: resolve(__dirname, '../web/editor'),
    emptyOutDir: true,
    sourcemap: true,
    target: 'es2022',
  },
  server: {
    port: 5175,
  },
});