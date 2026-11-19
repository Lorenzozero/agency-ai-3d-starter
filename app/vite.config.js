import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

const shim = (f) => fileURLToPath(new URL(`./src/shim/${f}.js`, import.meta.url))

// Base di Agentes IA 3D (ederland, MIT). Qui 'firebase/*' e' sostituito da src/shim: i dati stanno sul server hlab, non su Firebase.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { 'firebase/app': shim('app'), 'firebase/auth': shim('auth'), 'firebase/firestore': shim('firestore') } },
  // Il catalogo dei ruoli vive in ../roles. In sviluppo le chiamate /api vanno all'API locale.
  server: { fs: { allow: ['..'] }, proxy: { '/api': { target: process.env.HLAB_API || 'http://127.0.0.1:8770', changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 1200 },
})
