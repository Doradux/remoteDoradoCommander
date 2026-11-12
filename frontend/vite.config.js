import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({plugins:[react(),tailwindcss()],base:'/ui-assets/',build:{outDir:'../remote_dorado/static',emptyOutDir:true,manifest:true,assetsDir:'assets',rollupOptions:{input:'index.html'}}})
