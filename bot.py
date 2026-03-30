import os
import logging
from io import BytesIO
from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from datetime import date

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8791561190:AAEhUy4CwVyWJZgnM21LRqSgLEJd5InjdbY")

DOC_TYPES = ["Aadhaar", "Voter ID", "PAN", "Passport", "Driving Licence", "Other"]

# session states
WAITING_FRONT    = "waiting_front"
WAITING_DOC_TYPE = "waiting_doc_type"
WAITING_BACK     = "waiting_back"


def get_state(context):
    return context.user_data.get("state", WAITING_FRONT)

def set_state(context, state):
    context.user_data["state"] = state


def prepare_image(img):
    return img.convert("RGB")


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
        c.drawImage(ImageReader(img_buf), x_start, img_y, width=card_w, height=card_h,
                    preserveAspectRatio=True, anchor="c")

    top_y = A4_H - MARGIN - 38
    draw_card(front, "Front side", top_y)
    draw_card(back, "Back side", top_y - card_h - GAP - 20)
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.7, 0.7, 0.7)
    c.drawCentredString(A4_W / 2, 20, "ID Document Layout Bot")
    c.save()
    buf.seek(0)
    return buf


def doc_type_keyboard():
    buttons = [
        [InlineKeyboardButton("Aadhaar", callback_data="dt:Aadhaar"),
         InlineKeyboardButton("Voter ID", callback_data="dt:Voter ID")],
        [InlineKeyboardButton("PAN", callback_data="dt:PAN"),
         InlineKeyboardButton("Passport", callback_data="dt:Passport")],
        [InlineKeyboardButton("Driving Licence", callback_data="dt:Driving Licence"),
         InlineKeyboardButton("Other", callback_data="dt:Other")],
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes):
    context.user_data.clear()
    set_state(context, WAITING_FRONT)
    await update.message.reply_text(
        "Welcome to ID Document Layout Bot!\n\n"
        "Step 1: Send the *front* photo of your ID.\n"
        "Step 2: Choose the document type.\n"
        "Step 3: Send the *back* photo.\n\n"
        "I'll generate a print-ready A4 PDF!\n\n"
        "Use /reset to start over at any time.",
        parse_mode="Markdown"
    )


async def reset(update: Update, context: ContextTypes):
    context.user_data.clear()
    set_state(context, WAITING_FRONT)
    await update.message.reply_text("Reset done! Send the *front* photo of your ID.", parse_mode="Markdown")


async def handle_photo(update: Update, context: ContextTypes):
    state = get_state(context)

    # Download the photo
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        img_bytes = await file.download_as_bytearray()
        pil_img = Image.open(BytesIO(bytes(img_bytes)))
    except Exception as e:
        logger.error("Photo download failed: " + str(e))
        await update.message.reply_text("Could not read the photo. Please try again.")
        return

    if state == WAITING_FRONT:
        context.user_data["front"] = prepare_image(pil_img)
        set_state(context, WAITING_DOC_TYPE)
        await update.message.reply_text(
            "Front photo saved! What type of ID is this?",
            reply_markup=doc_type_keyboard()
        )

    elif state == WAITING_BACK:
        await update.message.reply_text("Generating your A4 PDF, please wait...")
        try:
            front = context.user_data["front"]
            doc_type = context.user_data.get("doc_type", "ID Document")
            pdf_buf = build_a4_pdf(front, prepare_image(pil_img), doc_type)
            filename = doc_type.replace(" ", "_") + "_A4.pdf"
            await update.message.reply_document(
                document=pdf_buf,
                filename=filename,
                caption=f"{doc_type} A4 Layout — Ready to print!"
            )
        except Exception as e:
            logger.error("PDF generation failed: " + str(e))
            await update.message.reply_text(
                "Something went wrong generating the PDF.\nUse /reset and try again."
            )
        finally:
            context.user_data.clear()
            set_state(context, WAITING_FRONT)

    elif state == WAITING_DOC_TYPE:
        await update.message.reply_text(
            "Please select the document type first using the buttons above.",
            reply_markup=doc_type_keyboard()
        )

    else:
        await update.message.reply_text("Please use /start or /reset to begin.")


async def handle_doc_type(update: Update, context: ContextTypes):
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("dt:"):
        return

    doc_type = query.data[3:]
    context.user_data["doc_type"] = doc_type
    set_state(context, WAITING_BACK)

    await query.edit_message_text(
        f"Document type: *{doc_type}*\n\nNow send the *back* photo of the ID.",
        parse_mode="Markdown"
    )


async def handle_text(update: Update, context: ContextTypes):
    state = get_state(context)
    if state == WAITING_FRONT:
        await update.message.reply_text("Please send the *front* photo of your ID to begin.", parse_mode="Markdown")
    elif state == WAITING_DOC_TYPE:
        await update.message.reply_text("Please tap a button above to select the document type.")
    elif state == WAITING_BACK:
        await update.message.reply_text("Please send the *back* photo of your ID.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Use /start to begin.")


app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CallbackQueryHandler(handle_doc_type, pattern="^dt:"))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

logger.info("Bot is running...")
app.run_polling()
