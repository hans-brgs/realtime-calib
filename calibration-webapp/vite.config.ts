import path from 'node:path';

import babel from '@rolldown/plugin-babel';
import react, { reactCompilerPreset } from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// React Compiler is enabled (ADR-0010): no manual memoization in components.
// Under Vite 8 (Rolldown) the compiler runs via @rolldown/plugin-babel + the
// reactCompilerPreset helper from @vitejs/plugin-react.
export default defineConfig({
  plugins: [react(), babel({ presets: [reactCompilerPreset()] })],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: true, // expose on the LAN for tablet access during dev
  },
});
