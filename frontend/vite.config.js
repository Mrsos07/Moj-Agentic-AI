import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    allowedHosts: true,
    proxy: {
      '/ws': {
        // 127.0.0.1 صراحةً: الباك إند يستمع على IPv4 فقط، وNode يحوّل localhost إلى ::1
        // (IPv6) أولاً مما يسبّب ECONNREFUSED في بروكسي الـ ws
        target: 'ws://127.0.0.1:8000',
        ws: true,
        // أثناء إعادة تشغيل الباك إند يرفض المنفذ 8000 الاتصال مؤقتاً —
        // اطبع سطراً هادئاً بدل Stack trace، والمتصفح يعيد الاتصال تلقائياً
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // ECONNRESET هو التوقيع الطبيعي لإعادة تشغيل الباك إند (WatchFiles)
            // أو تحديث الصفحة: كل السوكتات تُصفَّر دفعةً واحدة. طبعُه كـstack
            // trace كان يُغرق الطرفية ويُخفي الأخطاء الحقيقية.
            if (err?.code === 'ECONNREFUSED' || err?.errors) {
              console.log('[vite] backend (8000) غير متاح مؤقتاً — سيُعاد الاتصال تلقائياً')
            } else if (err?.code === 'ECONNRESET' || err?.code === 'EPIPE') {
              console.log('[vite] انقطع السوكت (إعادة تشغيل الباك إند أو تحديث الصفحة) — إعادة وصل تلقائية')
            } else {
              console.error('[vite] ws proxy error:', err)
            }
          })
        },
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})