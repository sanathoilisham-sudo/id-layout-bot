import os
import logging
from io import BytesIO
from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from datetime import date

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8791561190:AAEhUy4CwVyWJZgnM21LRqSgLEJd5InjdbY"

sessions = {}
DOC_TYPES = ["Aadhaar", "Voter ID", "PAN", "Passport", "Driving Licence", "Other"]

def crop_to_card(img):
    img = img.convert("RGB")
    TARGET_W, TARGET_H = 856, 540
    img_ratio = img.width / img.height
    target_ratio = TARGET_W / TARGET_H
    if img_ratio > target_ratio:
        new_w = TARGET_W
        new_h = int(TARGET_W / img_ratio)
    else:
        new_h = TARGET_H
        new_w = int(TARGET_H * img_ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (TARGET_W, TARGET_H), (255, 255, 255))
    canvas.paste(img, ((TARGET_W - new_w) // 2, (TARGET_H - new_h) // 2))
    return canvas

def build_a4_pdf(front, back, doc_type):
    A4_W, A4_H = A4
    MARGIN = 40
    GAP = 24
    usable_w = A4_W - 2 * MARGIN
    card_h = (A4_H - 2 * MARGIN - 38 - GAP - 40) / 2
    card_w = min(usable_w, card_h * 1.585)
    if card_w < card_h * 1.585:
        card_h = card_w / 1.585
    x_start = MARGIN + (usable_w - card_w) / 2
    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawCentredString(A4_W / 2, A4_H - MARGIN, doc_type + " - Front & Back")
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.6, 0.6, 0.6)
    c.drawCentredString(A4_W / 2, A4_H - MARGIN - 14, "Generated on " + date.today().strftime("%d %B %Y"))
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.line(MARGIN, A4_H - MARGIN - 24, A4_W - MARGIN, A4_H - MARGIN - 24)
    def draw_card(pil_img, label, y_top):
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(x_start, y_top - 12, label.upper())
        img_y = y_top - 18 - card_h
        img_buf = BytesIO()
        pil_img.save(img_buf, format="JPEG", quality=92)
        img_buf.seek(0)
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.setLineWidth(0.75)
        c.rect(x_start - 1, img_y - 1, card_w + 2, card_h + 2, stroke=1, fill=0)
        c.drawImage(ImageReader(img_buf), x_start, img_y, width=card_w, height=card_h, preserveAspectRatio=True, anchor="c")
    top_y = A4_H - MARGIN - 38
    draw_card(front, "Front side", top_y)
    draw_card(back, "Back side", top_y - card_h - GAP - 20)
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.7, 0.7, 0.7)
    c.drawCentredString(A4_W / 2, 20, "ID Document Layout Bot")
    c.save()
    buf.seek(0)
    return buf

async def start(update, context):
    await update.message.reply_text("Welcome to ID Document Layout Bot\n\nSend me:\n1. Front photo of your ID\n2. Back photo of your ID\n\nI will send back an A4 PDF ready to print!\n\nUse /reset to start over.")

async def reset(update, context):
    sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("Reset done! Send the front photo now.")

async def handle_photo(update, context):
    uid = update.effective_user.id
    photo = update.message.photo[-1]
    file = await photo.get_file()
    img_bytes = await file.download_as_bytearray()
    pil_img = Image.open(BytesIO(bytes(img_bytes)))
    caption = (update.message.caption or "").strip()
    doc_type = "ID Document"
    for dt in DOC_TYPES:
        if dt.lower() in caption.lower():
            doc_type = dt
            break
    if uid not in sessions:
        sessions[uid] = {"front": crop_to_card(pil_img), "type": doc_type}
        await update.message.reply_text("Front side saved! Now send the back side.")
    else:
        session = sessions.pop(uid)
        if doc_type == "ID Document" and session["type"] != "ID Document":
            doc_type = session["type"]
        await update.message.reply_text("Generating your A4 PDF...")
        try:
            pdf_buf = build_a4_pdf(session["front"], crop_to_card(pil_img), doc_type)
            await update.message.reply_document(document=pdf_buf, filename=doc_type.replace(" ", "_") + "_A4.pdf", caption=doc_type + " A4 Layout - Ready to print!")
        except Exception as e:
            logger.error("Error: " + str(e))
            await update.message.reply_text("Error generating PDF. Use /reset and try again.")

async def handle_text(update, context):
    await update.message.reply_text("Please send a photo of your ID. Use /start for help.")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
logger.info("Bot is running...")
app.run_polling()