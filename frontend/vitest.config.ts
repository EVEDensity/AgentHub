import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: [path.resolve(__dirname, '__tests__/vitest.setup.ts')],
    include: ['__tests__/**/*.{test,spec}.{ts,tsx}'],
    // Ensure relative imports from the frontend root resolve
    root: path.resolve(__dirname, '.'),
    // Allow CSS imports without crashes
    css: false,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
});
