import axios from 'axios'

/**
 * استنساخ عميق كمهمة خلفية: يبدأ المهمة ثم يتابع حالتها حتى تنتهي.
 * السحب العميق (مئات الردود من أشهر مختلفة) يستغرق دقائق — كان الطلب المتزامن يصطدم
 * بمهلة المتصفح (240-300 ثانية) فيفشل رغم أن الخادم ما زال يعمل.
 *
 * onProgress(text): وصف المرحلة الحالية كما يرسله الخادم (يُستدعى عند تغيّره فقط).
 * يعيد نفس شكل الاستجابة القديمة: { persona, voice_profile, fidelity, target, source }.
 */
export default async function clonePersona(body, { headers = {}, onProgress, maxMinutes = 12 } = {}) {
  const start = await axios.post('/api/aeos/persona/clone', { ...body, background: true }, { headers, timeout: 30000 })
  const jobId = start.data?.job_id
  if (!jobId) return start.data // خادم أقدم بلا مهام خلفية: النتيجة مباشرة
  const deadline = Date.now() + maxMinutes * 60000
  let last = ''
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 3000))
    const res = await axios.get(`/api/aeos/persona/clone/jobs/${jobId}`, { headers, timeout: 20000 })
    const job = res.data || {}
    if (job.progress && job.progress !== last) {
      last = job.progress
      onProgress?.(job.progress, job.elapsed)
    }
    if (job.status === 'done') return job.result || {}
    if (job.status === 'error') {
      const err = new Error(job.error || 'فشل الاستنساخ')
      err.response = { data: { detail: job.error } }
      throw err
    }
  }
  const err = new Error('timeout')
  err.code = 'ECONNABORTED'
  throw err
}
