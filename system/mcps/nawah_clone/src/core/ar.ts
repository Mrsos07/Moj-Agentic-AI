const DIGITS = "٠١٢٣٤٥٦٧٨٩";

/** Western digits → Eastern Arabic digits for UI text. */
export function ar(n: number | string): string {
  return String(n).replace(/\d/g, (d) => DIGITS[Number(d)]);
}

/** Arabic plural for منشور. */
export function posts(n: number): string {
  if (n === 0) return "لا منشورات";
  if (n === 1) return "منشور واحد";
  if (n === 2) return "منشوران";
  if (n >= 3 && n <= 10) return `${ar(n)} منشورات`;
  return `${ar(n)} منشورًا`;
}

/** Arabic plural for مقال. */
export function articles(n: number): string {
  if (n === 0) return "لا مقالات";
  if (n === 1) return "مقال واحد";
  if (n === 2) return "مقالان";
  if (n >= 3 && n <= 10) return `${ar(n)} مقالات`;
  return `${ar(n)} مقالًا`;
}

/** Arabic counted noun: count(3, "سطر", "سطران", "أسطر", "سطرًا") → "٣ أسطر". */
export function count(n: number, one: string, two: string, few: string, many: string): string {
  if (n === 0) return `لا ${few}`;
  if (n === 1) return one;
  if (n === 2) return two;
  if (n >= 3 && n <= 10) return `${ar(n)} ${few}`;
  return `${ar(n)} ${many}`;
}
export const lines = (n: number) => count(n, "سطر واحد", "سطران", "أسطر", "سطرًا");
export const paragraphs = (n: number) => count(n, "فقرة واحدة", "فقرتان", "فقرات", "فقرة");
export const words = (n: number) => count(n, "كلمة واحدة", "كلمتان", "كلمات", "كلمة");
export const stories = (n: number) => count(n, "خبر واحد", "خبران", "أخبار", "خبرًا");
export const outlets = (n: number) => count(n, "مصدر واحد", "مصدران", "مصادر", "مصدرًا");
export const videos = (n: number) => count(n, "فيديو واحد", "فيديوهان", "فيديوهات", "فيديو");
export const variants = (n: number) => count(n, "صيغة واحدة", "صيغتان", "صيغ", "صيغة");
/** "قبل ٣ دقائق" style relative time from an ISO timestamp; empty when unknown. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso.replace(" ", "T") + (iso.endsWith("Z") || /[+-]\d\d:\d\d$/.test(iso) ? "" : "Z")).getTime();
  const ms = Date.now() - t;
  if (!Number.isFinite(ms)) return "";
  const m = Math.max(1, Math.round(ms / 60000));
  if (m < 60) return `قبل ${count(m, "دقيقة", "دقيقتين", "دقائق", "دقيقة")}`;
  const h = Math.round(m / 60);
  if (h < 48) return `قبل ${count(h, "ساعة", "ساعتين", "ساعات", "ساعة")}`;
  return `قبل ${count(Math.round(h / 24), "يوم", "يومين", "أيام", "يومًا")}`;
}

export function initial(name: string): string {
  const t = name.trim();
  return t ? t[0].toUpperCase() : "؟";
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ar-SA-u-nu-arab", { day: "numeric", month: "long" }).format(d);
}
