import { useState } from 'react'

/** عرض «بصمة الصوت» المستنسخة: القياسات، الصوت، دليل الردود، الأمثلة، واختبار المطابقة. */
const pct = (v) => `${Math.round((v || 0) * 100)}%`
const LABELS = { agree: 'الموافق', disagree: 'المخالف', question: 'السائل', attack: 'المهاجم', praise: 'الشاكر', general: 'عموماً' }
const KINDS = { courtesy: 'مجاملة/دعاء', agree: 'موافقة', disagree: 'اعتراض', question: 'سؤال', humor: 'دعابة', reaction: 'تعليق قصير', substantive: 'تعليق بمضمون' }
const CHECKS = { length: 'الطول', emoji: 'الإيموجي', dialect: 'اللهجة', reply_length: 'طول الرد', original: 'غير منسوخ' }

const Row = ({ k, children }) => (
  <div className="flex gap-1.5 text-[11px]">
    <span className="shrink-0 font-semibold text-text-primary-light dark:text-text-primary-dark">{k}:</span>
    <span className="text-text-secondary-light dark:text-text-secondary-dark">{children}</span>
  </div>
)

const VoiceProfileView = ({ profile, pending, fidelity }) => {
  const [open, setOpen] = useState(false)
  if (!profile?.version) return null
  const st = profile.style || {}
  const a = profile.analysis || {}
  const ex = profile.exemplars || {}
  const s = profile.sample || {}

  return (
    <div className="rounded-lg border border-purple-300/60 dark:border-purple-500/30 bg-white/60 dark:bg-black/10 p-2 space-y-1.5">
      <button type="button" onClick={() => setOpen(o => !o)} className="w-full flex items-center justify-between text-[11px] font-semibold text-purple-700 dark:text-purple-300">
        <span>
          🎙️ بصمة الصوت {profile.source_handle ? `(من ${profile.source_handle})` : ''}
          {pending && <span className="mr-1 text-amber-600"> — غير محفوظة بعد</span>}
          {fidelity?.score != null && <span className="mr-1"> · مطابقة {fidelity.score}%</span>}
          {fidelity?.nawah_score != null && <span className="mr-1"> · نواه {fidelity.nawah_score}/100</span>}
        </span>
        <span>{open ? '▲' : '▼'}</span>
      </button>
      <div className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
        عيّنة: {s.tweets || 0} تغريدة · {s.replies || 0} رد ({s.pairs || 0} مع سياقها){s.threads ? ` · ${s.threads} ثريد` : ''}
        {s.coverage?.from && ` · من ${s.coverage.from} إلى ${s.coverage.to} (${s.coverage.days} يوماً، حُلّل ${s.analyzed_replies} رداً)`}
        {profile.version < 2 && <span className="mr-1 text-amber-600"> — استنساخ قديم سطحي، أعد الاستنساخ للعمق</span>}
      </div>
      {open && (
        <div className="space-y-2 pt-1">
          {a.voice_summary && <p className="text-[11px] leading-relaxed text-text-primary-light dark:text-text-primary-dark">{a.voice_summary}</p>}
          {profile.nawah_error && (
            <div className="text-[10px] text-amber-600">⚠️ نواه لم تعمل فبُنيت البصمة بالمحلّل القديم: {profile.nawah_error}</div>
          )}
          {(a.style_guide?.length > 0 || profile.nawah) && (
            <div className="rounded border border-purple-300/50 dark:border-purple-500/30 p-1.5 space-y-0.5">
              <div className="text-[11px] font-semibold">
                🧬 بصمة نواه{a.dialect_family ? ` · ${a.dialect_family}` : ''}{profile.nawah?.model ? ` · ${profile.nawah.model}` : ''}
                {profile.nawah?.source_count ? ` · ${profile.nawah.source_count} نصاً` : ''}
              </div>
              {(a.style_guide || []).map((g, i) => (
                <div key={i} className="text-[10px] leading-relaxed text-text-primary-light dark:text-text-primary-dark">- {g}</div>
              ))}
              {a.never_uses?.length > 0 && <Row k="لا يفعل أبداً">{a.never_uses.join('؛ ')}</Row>}
              {a.style_notes && Object.values(a.style_notes).length > 0 && <Row k="ملاحظات">{Object.values(a.style_notes).join(' ')}</Row>}
            </div>
          )}
          {a.identity_core?.length > 0 && <Row k="ثوابت هويته">{a.identity_core.join('؛ ')}</Row>}
          {st.reply_kinds && Object.keys(st.reply_kinds).length > 0 && (
            <Row k="أنواع ردوده">
              {Object.entries(st.reply_kinds).map(([k, v]) => `${KINDS[k] || k} ${pct(v.share)} (≈${v.len_median} حرفاً)`).join(' · ')}
            </Row>
          )}
          {(a.era_topics?.length > 0 || st.era_phrases?.length > 0) && (
            <Row k="مواضيع مرحلة (لا تُقلَّد)">{(a.era_topics?.length ? a.era_topics : st.era_phrases).slice(0, 6).join('، ')}</Row>
          )}
          <div className="space-y-0.5">
            <Row k="الطول">{st.len_p25}–{st.len_p75} حرفاً (الردود ≈ {st.reply_len_median})</Row>
            <Row k="اللهجة">{st.dialect_guess || '—'}{a.register ? ` · ${a.register}` : ''}</Row>
            <Row k="الإيموجي">{pct(st.emoji_rate)} {(st.top_emojis || []).join(' ')} · هاشتاق {pct(st.hashtag_rate)} · أسئلة {pct(st.question_rate)}</Row>
            {st.openers?.length > 0 && <Row k="مطالعه">{st.openers.join('، ')}</Row>}
            {st.signature_phrases?.length > 0 && <Row k="عباراته">{st.signature_phrases.slice(0, 8).join('، ')}</Row>}
            {a.humor && <Row k="الدعابة">{a.humor}</Row>}
            {a.stance && <Row k="الموقف">{a.stance}</Row>}
            {st.active_hours_utc?.length > 0 && <Row k="ساعات نشاطه (UTC)">{st.active_hours_utc.join('، ')}</Row>}
          </div>
          {a.pillars?.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold mb-0.5">المحاور</div>
              {a.pillars.map(p => <Row key={p.name} k={`${p.name} (${pct(p.share)})`}>{p.angle}</Row>)}
            </div>
          )}
          {a.reply_playbook && Object.keys(a.reply_playbook).length > 0 && (
            <div>
              <div className="text-[11px] font-semibold mb-0.5">دليل ردوده</div>
              {Object.entries(a.reply_playbook).map(([k, v]) => <Row key={k} k={`مع ${LABELS[k] || k}`}>{v}</Row>)}
            </div>
          )}
          {a.red_lines?.length > 0 && <Row k="خطوطه الحمراء">{a.red_lines.join('؛ ')}</Row>}
          {ex.replies?.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold mb-0.5">أمثلة ردود حقيقية (مرجع أسلوب فقط)</div>
              {ex.replies.slice(0, 3).map((p, i) => (
                <div key={i} className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">↩ «{p.to.slice(0, 80)}» ← <b>«{p.reply}»</b></div>
              ))}
            </div>
          )}
          {fidelity?.samples && (
            <div className="rounded border border-dashed border-purple-400/50 p-1.5 space-y-1">
              <div className="text-[11px] font-semibold">اختبار: هكذا سيكتب الموظف بالهوية الجديدة</div>
              {(fidelity.samples.posts || []).map((p, i) => (
                <div key={i} className="text-[11px] whitespace-pre-wrap text-text-primary-light dark:text-text-primary-dark">📝 {p}</div>
              ))}
              {fidelity.samples.reply?.text && (
                <div className="text-[11px] text-text-primary-light dark:text-text-primary-dark">
                  💬 على «{(fidelity.samples.reply.to || '').slice(0, 80)}»: <b>{fidelity.samples.reply.text}</b>
                </div>
              )}
              <div className="flex flex-wrap gap-1">
                {Object.entries(fidelity.checks || {}).map(([k, ok]) => (
                  <span key={k} className={`text-[10px] px-1.5 py-0.5 rounded-full ${ok ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300' : 'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-300'}`}>
                    {ok ? '✓' : '✗'} {CHECKS[k] || k}
                  </span>
                ))}
              </div>
            </div>
          )}
          <p className="text-[10px] text-text-secondary-light dark:text-text-secondary-dark">
            تُنقل الهوية (الأسلوب والشخصية وطريقة التفاعل) لا اسم الحساب الأصلي، والنصوص الحقيقية مرجع للأسلوب فقط — النسخ الحرفي يُمنع من النشر.
          </p>
        </div>
      )}
    </div>
  )
}

export default VoiceProfileView
