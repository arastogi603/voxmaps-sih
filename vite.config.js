import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ['react', 'react-dom', 'three', '@react-three/fiber', '@react-three/drei'] },
  worker: { format: 'es' },
  optimizeDeps: { exclude: ['maplibre-gl/dist/maplibre-gl-worker.mjs'] },
  server: { proxy: { '/api': 'http://127.0.0.1:8766' } },
  preview: { proxy: { '/api': 'http://127.0.0.1:8766' } },
});
