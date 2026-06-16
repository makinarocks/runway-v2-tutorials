import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: true,
    proxy: {
      '/api/inference': {
        target: 'https://inference.<your-runway-domain>',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/api\/inference/, '/api'),
      },
      '/api/airflow': {
        target: 'https://airflow.<your-runway-domain>',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/api\/airflow/, ''),
      },
    },
  },
});
