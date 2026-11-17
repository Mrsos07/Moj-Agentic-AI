import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// منفذ الباك إند: BACKEND_PORT=8010 عند تشغيل نسخة على منفذ آخر (8000 افتراضياً)
const BACKEND_PORT = process.env.BACKEND_PORT || '8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // هذا الجهاز فقط (VITE_HOST=0.0.0.0 للإتاحة على الشبكة صراحةً)
    host: process.env.VITE_HOST || '127.0.0.1',
    port: 3000,
    allowedHosts: process.env.VITE_HOST ? true : ['localhost', '127.0.0.1'],
    proxy: {
      '/ws': {
        // 127.0.0.1 صراحةً: الباك إند يستمع على IPv4 فقط، وNode يحوّل localhost إلى ::1
        // (IPv6) أولاً مما يسبّب ECONNREFUSED في بروكسي الـ ws
        target: `ws://127.0.0.1:${BACKEND_PORT}`,
        ws: true,
        // أثناء إعادة تشغيل الباك إند يرفض المنفذ 8000 الاتصال مؤقتاً —
        // اطبع سطراً هادئاً بدل Stack trace، والمتصفح يعيد الاتصال تلقائياً
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // ECONNRESET هو التوقيع الطبيعي لإعادة تشغيل الباك إند (WatchFiles)
            // أو تحديث الصفحة: كل السوكتات تُصفَّر دفعةً واحدة. طبعُه كـstack
            // trace كان يُغرق الطرفية ويُخفي الأخطاء الحقيقية.
            if (err?.code === 'ECONNREFUSED' || err?.errors) {
              console.log(`[vite] backend (${BACKEND_PORT}) غير متاح مؤقتاً — سيُعاد الاتصال تلقائياً`)
            } else if (err?.code === 'ECONNRESET' || err?.code === 'EPIPE') {
              console.log('[vite] انقطع السوكت (إعادة تشغيل الباك إند أو تحديث الصفحة) — إعادة وصل تلقائية')
            } else {
              console.error('[vite] ws proxy error:', err)
            }
          })
        },
      },
      '/api': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
      // ملفات الباك إند: صور معاينة الهوية (/static/generated) ومرفقات الشات (/static/uploads) —
      // nginx يمرّرها في الإنتاج، وبدون هذا لا تصل الصورة للمتصفح في التطوير
      '/static': {
        target: `http://127.0.0.1:${BACKEND_PORT}`,
        changeOrigin: true,
      },
    },
  },
})