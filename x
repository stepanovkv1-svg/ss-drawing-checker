{
 "version": "0.1.0-e1.2",
 "intake": [
  {
   "name": "null",
   "status": "ошибка",
   "message": "null: тип файла не поддерживается (нужны PDF, XLSX, MD или DOCX).",
   "pages": 0,
   "extract": null
  },
  {
   "name": "null",
   "status": "ошибка",
   "message": "null: тип файла не поддерживается (нужны PDF, XLSX, MD или DOCX).",
   "pages": 0,
   "extract": null
  }
 ],
 "only_local": true,
 "summary": {
  "files": 0,
  "ok": 0,
  "bad": 0,
  "pages": 0
 },
 "pages": [],
 "tesseract": "/usr/bin/tesseract",
 "ok": false,
 "error": "Traceback (most recent call last):\n  File \"/home/claude/ss-drawing-checker/app/main.py\", line 31, in selftest\n    pdf_file = obj.files()[0]\n               ~~~~~~~~~~~^^^\nIndexError: list index out of range\n"
}