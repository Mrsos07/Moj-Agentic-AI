export const DEMO_TASK = {
  id: 'demo-task-1',
  title: 'نشر منشور يومي عن الاقتصاد',
  status: 'running',
  progress: 45,
  startedAt: new Date(Date.now() - 1000 * 60 * 12).toISOString(),
}

export const DEMO_EVENTS = [
  {
    id: 'ev-1',
    taskId: 'demo-task-1',
    title: 'استلام المهمة من المنسّق',
    detail: 'حُدّد الموضوع وزاوية الكتابة حسب تخصص الوكيل.',
    timestamp: new Date(Date.now() - 1000 * 60 * 12).toISOString(),
    level: 'info',
  },
  {
    id: 'ev-2',
    taskId: 'demo-task-1',
    title: 'جمع المصادر',
    detail: 'قراءة الترند والأخبار المرتبطة بالتخصص من المتابَعين.',
    timestamp: new Date(Date.now() - 1000 * 60 * 10).toISOString(),
    level: 'info',
  },
  {
    id: 'ev-3',
    taskId: 'demo-task-1',
    title: 'صياغة المسودة الأولى',
    detail: 'هذه تفاصيل طويلة لاختبار التفاف النص داخل البطاقة على العرض الضيق والعريض، حتى نتأكد أن الأسطر الطويلة لا تكسر التخطيط ولا تتجاوز حدود الحاوية وأن النص العربي المختلط بالمصطلحات الإنجليزية مثل OpenRouter و GPT يبقى مقروءاً بشكل طبيعي.',
    timestamp: new Date(Date.now() - 1000 * 60 * 8).toISOString(),
    level: 'success',
  },
  {
    id: 'ev-4',
    taskId: 'demo-task-1',
    title: 'مراجعة الحارس اللغوي',
    detail: 'رُفضت صياغة مبالغ فيها وأُعيدت الكتابة بنبرة أهدأ.',
    timestamp: new Date(Date.now() - 1000 * 60 * 6).toISOString(),
    level: 'warning',
  },
  {
    id: 'ev-5',
    taskId: 'demo-task-1',
    title: 'تعذّر جلب إحصائية الحساب',
    detail: 'ستُعاد المحاولة لاحقاً؛ لا يوقف النشر.',
    timestamp: new Date(Date.now() - 1000 * 60 * 4).toISOString(),
    level: 'error',
  },
  {
    id: 'ev-6',
    taskId: 'demo-task-1',
    title: 'جدولة النشر النهائي',
  },
]
