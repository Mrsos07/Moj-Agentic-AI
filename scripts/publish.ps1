<#
نشر آمن بأمر واحد: GitHub (main) ثم نسخة OneDrive — بنفس الفحوص في كل مرة.

    .\scripts\publish.ps1 -Message "وصف التغيير"

1. يجهّز كل التغييرات (git add -A) ويرفض النشر إن وُجد ملف ممنوع (.env، data/، كوكيز،
   قاعدة بيانات، __pycache__) أو نمط مفتاح سري في الفروق (Tavily، TrendsMCP، OpenRouter، OpenAI،
   GitHub، مفاتيح خاصة) — فيُلغي التجهيز ويتوقف.
2. commit عادي، ثم push بلا force وبعد التحقق أن الرفع fast-forward فوق GitHub.
3. يحدّث نسخة OneDrive بـ git pull --ff-only — فقط إن كانت بلا تعديلات محلية (لا يكتب فوق شيء).
4. يطبع رقم الـcommit في GitHub والمجلدين للتأكد أنها واحدة.

مسار نسخة OneDrive: المتغيّر MOJ_ONEDRIVE_COPY، وإلا %USERPROFILE%\OneDrive\Desktop\Moj-Agentic-AI.
#>
param(
    [string]$Message = "",
    [string]$OneDrive = $(if ($env:MOJ_ONEDRIVE_COPY) { $env:MOJ_ONEDRIVE_COPY } else { Join-Path $env:USERPROFILE "OneDrive\Desktop\Moj-Agentic-AI" })
)
# Continue لا Stop: git يكتب تحذيرات عادية (LF/CRLF) على stderr، وفي PowerShell 5.1 مع Stop
# تتحوّل إلى خطأ قاتل. كل خطوة تتحقق من $LASTEXITCODE صراحةً.
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:GIT_CONFIG_PARAMETERS = "'core.safecrlf=false'"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Fail($msg) { Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

# هوية الكاتب: إعداد git إن وُجد، وإلا هوية آخر commit في المستودع (لا تعديل لإعدادات git)
if (-not (git config user.email)) {
    $env:GIT_AUTHOR_NAME = git log -1 --format='%an'; $env:GIT_AUTHOR_EMAIL = git log -1 --format='%ae'
    $env:GIT_COMMITTER_NAME = $env:GIT_AUTHOR_NAME; $env:GIT_COMMITTER_EMAIL = $env:GIT_AUTHOR_EMAIL
}

git fetch -q origin
if ($LASTEXITCODE -ne 0) { Fail "تعذّر الاتصال بـ GitHub (git fetch)" }

git add -A
$staged = @(git diff --cached --name-only)
# مسارات ممنوعة: جلسات X (كوكيز/ملفات تعريف المتصفح)، الأسرار، قواعد البيانات
$forbiddenRe = '(^|/)\.env($|\.(?!.*example$))|^data/|cookie|debug_.*\.html$|\.har$|chrome_profile|storage_state|\.db$|\.sqlite3?$|__pycache__|\.pyc$|system_mcp_secrets|internal_secrets'
# مفاتيح المزوّدين + قيم جلسات X (auth_token 40 hex، ct0 ≥32 hex) بأي صيغة (JSON/نص)
$secretRe = 'tmcp_live_[A-Za-z0-9]{8}|tvly-(dev-)?[A-Za-z0-9-]{16}|sk-or-v1-[A-Za-z0-9]{20}|sk-(proj-)?[A-Za-z0-9_-]{32}|ghp_[A-Za-z0-9]{30}|github_pat_[A-Za-z0-9_]{30}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|auth_token[^0-9a-f\r\n]{0,40}[0-9a-f]{40}|ct0[^0-9a-f\r\n]{0,40}[0-9a-f]{32,}'
# شكل ملف تصدير كوكيز X (القيمة في السطر التالي) — لغير ملفات الكود (الكود يبنيه من متغيرات)
$cookieExportRe = '"name"\s*:\s*"(auth_token|ct0|twid|kdt)"'
if ($staged.Count) {
    # الحذف من المستودع (إخراج ملف ممنوع من التتبّع) مسموح؛ الإضافة/التعديل لا
    $forbidden = @(git diff --cached --name-only --diff-filter=d) | Where-Object { $_ -match $forbiddenRe }
    if ($forbidden) { git reset -q; Fail "ملفات ممنوعة في التغييرات — لم يُنشر شيء:`n  $($forbidden -join "`n  ")" }
    $hits = @(git diff --cached -U0 | Select-String -Pattern $secretRe | Where-Object { $_.Line -like '+*' }) +
            @(git diff --cached -U0 -- . ':(exclude)*.py' | Select-String -Pattern $cookieExportRe | Where-Object { $_.Line -like '+*' })
    if ($hits) { git reset -q; Fail "نمط مفتاح سري في الفروق — لم يُنشر شيء (السطور: $($hits.Count)). انقل القيمة إلى .env أو data/." }

    if (-not $Message) { $Message = "تحديث: " + (($staged | ForEach-Object { Split-Path $_ -Leaf } | Select-Object -First 6) -join "، ") }
    $body = "$Message`n`nGenerated with [Devin](https://devin.ai)`n`nCo-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>"
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllText($tmp, $body, (New-Object Text.UTF8Encoding $false))
    git commit -q -F $tmp; $rc = $LASTEXITCODE; Remove-Item $tmp
    if ($rc -ne 0) { Fail "فشل الـcommit (ربما hook) — راجع الرسالة أعلاه" }
    Write-Host "✅ commit: $($staged.Count) ملفاً" -ForegroundColor Green
} else {
    Write-Host "لا تغييرات جديدة للـcommit"
}

git merge-base --is-ancestor origin/main main
if ($LASTEXITCODE -ne 0) { Fail "GitHub فيه commits ليست هنا — اسحبها أولاً (git pull --ff-only). لا force push." }
$ahead = [int](git rev-list --count origin/main..main)
if ($ahead -gt 0) {
    # فحص الشجرة كاملة قبل الرفع: ملف ممنوع تُتبّع قبل قاعدة .gitignore لا يظهر في الفروق
    # (هكذا رُفعت كوكيز X: app/x/loginx2026/cookies و loginx/CookiesBackup)
    $treeBad = @(git ls-tree -r --name-only main) | Where-Object { $_ -match $forbiddenRe -and $_ -notmatch '\.(py|html|md)$' }
    if ($treeBad) { Fail "ملفات ممنوعة متتبّعة في main — لم يُرفع شيء:`n  $($treeBad -join "`n  ")`nأخرجها: git rm -r --cached <path>" }
    $treeHits = @(git grep -I -l -E $secretRe main -- . 2>$null) + @(git grep -I -l -E $cookieExportRe main -- . ':(exclude)*.py' 2>$null)
    if ($treeHits) { Fail "نمط مفتاح/جلسة في ملفات متتبّعة — لم يُرفع شيء:`n  $($treeHits -join "`n  ")" }
    git push -q origin main
    if ($LASTEXITCODE -ne 0) { Fail "فشل الرفع إلى GitHub" }
    Write-Host "✅ رُفع إلى GitHub ($ahead commit)" -ForegroundColor Green
} else {
    Write-Host "GitHub محدّث مسبقاً"
}

if (Test-Path (Join-Path $OneDrive ".git")) {
    $dirty = @(git -C $OneDrive status --porcelain)
    if ($dirty.Count) {
        Write-Host "⚠️ نسخة OneDrive فيها $($dirty.Count) تعديلاً محلياً — لم أحدّثها حتى لا أكتب فوقها:" -ForegroundColor Yellow
        $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "   $_" }
    } else {
        git -C $OneDrive pull -q --ff-only origin main
        if ($LASTEXITCODE -ne 0) { Fail "تعذّر تحديث نسخة OneDrive بـ fast-forward" }
        Write-Host "✅ حُدّثت نسخة OneDrive" -ForegroundColor Green
    }
} else {
    Write-Host "⚠️ لا توجد نسخة OneDrive في $OneDrive — تخطّيت التحديث" -ForegroundColor Yellow
}

$gh = ((git ls-remote origin refs/heads/main) -split "`t")[0].Substring(0, 8)
$pj = (git rev-parse --short=8 HEAD)
$od = if (Test-Path (Join-Path $OneDrive ".git")) { git -C $OneDrive rev-parse --short=8 HEAD } else { "-" }
Write-Host "GitHub=$gh | Projects=$pj | OneDrive=$od"
if ($gh -ne $pj -or ($od -ne "-" -and $od -ne $pj)) { exit 2 }
