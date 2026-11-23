const arabic = {
  "Al Qubtan Restaurants Radio": "إذاعة مطاعم القبطان",
  "Station": "المحطة",
  "Listen": "استمع",
  "Playlist Management": "إدارة قائمة التشغيل",
  "Song Discovery": "اكتشاف الأغاني",
  "Ready recordings": "التسجيلات الجاهزة",
  "Song actions": "خيارات الأغنية",
  "View in Playlist Management": "عرض في إدارة قائمة التشغيل",
  "Play": "تشغيل",
  "Stop": "إيقاف",
  "Ready to play": "جاهز للتشغيل",
  "Station status unavailable": "حالة المحطة غير متاحة",
  "Playback interrupted. Press Play to try again.": "انقطع التشغيل. اضغط على تشغيل للمحاولة مجددًا.",
  "On air": "على الهواء",
  "Up next": "التالي",
  "Music credits": "الأغاني وفنانوها",
  "Manager sign-in": "دخول المدير",
  "Username": "اسم المستخدم",
  "Password": "كلمة المرور",
  "Sign in": "تسجيل الدخول",
  "Sign out": "تسجيل الخروج",
  "Recording (MP3, WAV, or M4A)": "التسجيل (MP3 أو WAV أو M4A)",
  "Choose file": "اختر ملفًا",
  "Prepare recording": "تجهيز التسجيل",
  "Title": "العنوان",
  "Artist": "الفنان",
  "Save details": "حفظ التفاصيل",
  "Close": "إغلاق",
  "Search by title or artist": "البحث بالعنوان أو الفنان",
  "Search": "بحث",
  "Music library": "المكتبة الموسيقية",
  "YouTube results": "نتائج يوتيوب",
  "Searching YouTube…": "جارٍ البحث في يوتيوب…",
  "Import to library": "استيراد إلى المكتبة",
  "Preview": "معاينة",
  "Edit details": "تعديل التفاصيل",
  "Add to end": "إضافة إلى نهاية القائمة",
  "Private preview": "معاينة خاصة",
  "Close preview": "إغلاق المعاينة",
  "No matching recordings.": "لا توجد تسجيلات مطابقة.",
  "Sign in to continue managing the station.": "سجّل الدخول لمتابعة إدارة المحطة.",
  "Incorrect username or password.": "اسم المستخدم أو كلمة المرور غير صحيحة.",
  "Request failed. Try again.": "تعذّر تنفيذ الطلب. حاول مجددًا.",
  "Preparing recording…": "جارٍ تجهيز التسجيل…",
  "Recording ready in the library.": "التسجيل جاهز في المكتبة.",
  "This recording is already in the library.": "هذا التسجيل موجود بالفعل في المكتبة.",
  "Details saved.": "تم حفظ التفاصيل.",
  "Added to the end of the playlist.": "تمت الإضافة إلى نهاية قائمة التشغيل.",
  "Could not play the preview.": "تعذّر تشغيل المعاينة.",
  "Choose an MP3, WAV, or M4A file.": "اختر ملفًا بصيغة MP3 أو WAV أو M4A.",
  "The file exceeds 100 MB.": "حجم الملف يتجاوز 100 ميغابايت.",
  "Another recording is being prepared. Try again shortly.": "يجري تجهيز تسجيل آخر. حاول مرة أخرى بعد قليل.",
  "There is not enough disk space to prepare the file.": "لا توجد مساحة كافية لتجهيز الملف.",
  "Upload interrupted. Try again.": "انقطع الرفع. حاول مجددًا.",
  "Could not prepare audio from this file.": "تعذّر تجهيز الصوت من هذا الملف.",
  "Audio preparation is unavailable.": "خدمة تجهيز الصوت غير متاحة.",
  "This recording is no longer available.": "هذا التسجيل لم يعد متاحًا.",
  "YouTube search is unavailable. You can still upload a recording.": "البحث في يوتيوب غير متاح. لا يزال بإمكانك رفع تسجيل.",
  "YouTube search is busy. Try again shortly.": "البحث في يوتيوب مشغول. حاول مرة أخرى بعد قليل.",
  "Could not retrieve this YouTube recording. You can upload a file instead.": "تعذّر جلب هذا التسجيل من يوتيوب. يمكنك رفع ملف بدلًا من ذلك.",
  "Temporary previews are full. Try again in a few minutes.": "امتلأت مساحة المعاينات المؤقتة. حاول مرة أخرى بعد بضع دقائق.",
  "Enter a title of up to 300 characters and an optional artist.": "أدخل عنوانًا لا يتجاوز 300 حرف واسم الفنان إن توفر.",
};

let language = "en";
try {
  if (localStorage.getItem("radio-language") === "ar") {
    language = "ar";
  }
} catch {
  // The switch also works in browsers that block local storage.
}

const switcher = document.querySelector("#language");

export function t(label) {
  return language === "ar" ? (arabic[label] ?? label) : label;
}

export function setLabel(element, label) {
  element.dataset.i18n = label;
  element.textContent = t(label);
}

function applyLanguage() {
  const rtl = language === "ar";
  document.documentElement.lang = language;
  document.documentElement.dir = rtl ? "rtl" : "ltr";
  document.title = t("Al Qubtan Restaurants Radio");
  switcher.lang = rtl ? "en" : "ar";
  switcher.textContent = rtl ? "English" : "العربية";
  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of document.querySelectorAll("[data-i18n-aria-label]")) {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  }
}

switcher.addEventListener("click", () => {
  language = language === "en" ? "ar" : "en";
  try {
    localStorage.setItem("radio-language", language);
  } catch {
    // The current page still uses the new language.
  }
  applyLanguage();
});

applyLanguage();
