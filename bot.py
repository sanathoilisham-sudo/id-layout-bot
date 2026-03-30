import os
import asyncio
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

WAITING_FRONT    = "waiting_front"
WAITING_DOC_TYPE = "waiting_doc_type"
WAITING_BACK     = "waiting_back"

# media_group_id -> {"uid", "chat_id", "images": [], "task"}
media_groups: dict = {}


# ── PDF builder ───────────────────────────────────────────────────────────────

def build_a4_pdf(front: Image.Image, back: Image.Image, doc_type: str) -> BytesIO:
    A4_W, A4_H = A4          # 595 x 842 pt
    MARGIN = 40
    GAP    = 28
    HEADER = 38              # space for title + date

    # Standard ID card: 85.6mm x 54mm → 242.7pt x 153.1pt
    # Scale up 1.9× so cards are large and clear on A4 print
    MM_TO_PT = 72 / 25.4
    card_w = 85.6 * MM_TO_PT * 1.9   # ≈ 461 pt
    card_h = 54.0 * MM_TO_PT * 1.9   # ≈ 291 pt

    # If card_w exceeds usable width, scale down to fit
    usable_w = A4_W - 2 * MARGIN
    if card_w > usable_w:
        scale = usable_w / card_w
        card_w *= scale
        card_h *= scale

    x_start = MARGIN + (usable_w - card_w) / 2

    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)

    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.2, 0.2, 0.2)
    c.drawCentredString(A4_W / 2, A4_H - MARGIN, doc_type + " - Front & Back")
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.6, 0.6, 0.6)
    c.drawCentredString(A4_W / 2, A4_H - MARGIN - 14,
                        "Generated on " + date.today().strftime("%d %B %Y"))
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
        c.drawImage(ImageReader(img_buf), x_start, img_y,
                    width=card_w, height=card_h, preserveAspectRatio=True, anchor="c")

    top_y = A4_H - MARGIN - 38
    draw_card(front, "Front side", top_y)
    draw_card(back,  "Back side",  top_y - card_h - GAP - 20)

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.7, 0.7, 0.7)
    c.drawCentredString(A4_W / 2, 20, "ID Document Layout Bot")
    c.save()
    buf.seek(0)
    return buf


# ── Helpers ───────────────────────────────────────────────────────────────────

def doc_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Aadhaar",          callback_data="dt:Aadhaar"),
         InlineKeyboardButton("Voter ID",          callback_data="dt:Voter ID")],
        [InlineKeyboardButton("PAN",               callback_data="dt:PAN"),
         InlineKeyboardButton("Passport",          callback_data="dt:Passport")],
        [InlineKeyboardButton("Driving Licence",   callback_data="dt:Driving Licence"),
         InlineKeyboardButton("Other",             callback_data="dt:Other")],
    ])

async def download_image(photo_obj) -> Image.Image:
    file = await photo_obj.get_file()
    data = await file.download_as_bytearray()
    return Image.open(BytesIO(bytes(data)))

async def generate_and_send_pdf(bot, chat_id, front_img, back_img, doc_type):
    await bot.send_message(chat_id, "Generating PDF, please wait...")
    front = front_img.convert("RGB")
    back  = back_img.convert("RGB")
    pdf_buf  = build_a4_pdf(front, back, doc_type)
    filename = doc_type.replace(" ", "_") + "_A4.pdf"
    await bot.send_document(
        chat_id=chat_id,
        document=pdf_buf,
        filename=filename,
        caption=f"{doc_type} A4 Layout — Ready to print!"
    )


# ── Media group collector ─────────────────────────────────────────────────────

async def process_media_group(context: ContextTypes.DEFAULT_TYPE):
    """Called 1.5 s after the first photo of an album arrives."""
    job   = context.job
    mgid  = job.data["media_group_id"]
    uid   = job.data["uid"]
    chat_id = job.data["chat_id"]

    group = media_groups.pop(mgid, None)
    if not group:
        return

    images = group["images"]
    if len(images) < 2:
        await context.bot.send_message(
            chat_id,
            "Please send *both* front and back photos together as an album (2 photos).",
            parse_mode="Markdown"
        )
        return

    doc_type = context.application.user_data.get(uid, {}).get("doc_type", "ID Document")

    try:
        await generate_and_send_pdf(context.bot, chat_id, images[0], images[1], doc_type)
    except Exception as e:
        logger.error(f"Album PDF error: {e}")
        await context.bot.send_message(chat_id, "Error generating PDF. Use /reset and try again.")
    finally:
        context.application.user_data.get(uid, {}).clear()


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Welcome to *ID Document Layout Bot!*\n\n"
        "Tip: Crop your photo to just the card before sending for best results.\n\n"
        "*Option A — Album (faster):*\n"
        "Select both front & back photos and send together as an album.\n"
        "Then tap the doc type button.\n\n"
        "*Option B — One by one:*\n"
        "1. Send front photo → pick doc type\n"
        "2. Send back photo → get PDF\n\n"
        "Use /reset to start over.",
        parse_mode="Markdown"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Reset done! Send front photo or an album of front+back.",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    chat_id = update.effective_chat.id
    mgid    = update.message.media_group_id

    # ── Album (2 photos sent together) ──────────────────────────────────────
    if mgid:
        try:
            pil_img = await download_image(update.message.photo[-1])
        except Exception as e:
            logger.error(f"Album photo download failed: {e}")
            return

        if mgid not in media_groups:
            media_groups[mgid] = {"uid": uid, "chat_id": chat_id, "images": []}
            # Fire process_media_group after 1.5 s to collect all album photos
            context.job_queue.run_once(
                process_media_group,
                when=1.5,
                data={"media_group_id": mgid, "uid": uid, "chat_id": chat_id},
                name=f"mg_{mgid}"
            )

        media_groups[mgid]["images"].append(pil_img)

        # After collecting first photo, ask doc type (only once per album)
        if len(media_groups[mgid]["images"]) == 1:
            await update.message.reply_text(
                "Album received! What type of ID is this?",
                reply_markup=doc_type_keyboard()
            )
        return

    # ── Single photo flow ────────────────────────────────────────────────────
    try:
        pil_img = await download_image(update.message.photo[-1])
    except Exception as e:
        logger.error(f"Photo download failed: {e}")
        await update.message.reply_text("Could not read the photo. Please try again.")
        return

    state = context.user_data.get("state", WAITING_FRONT)

    if state == WAITING_FRONT:
        context.user_data["front"] = pil_img
        context.user_data["state"] = WAITING_DOC_TYPE
        await update.message.reply_text(
            "Front photo saved! What type of ID is this?",
            reply_markup=doc_type_keyboard()
        )

    elif state == WAITING_DOC_TYPE:
        await update.message.reply_text(
            "Please tap a button above to select the document type first.",
            reply_markup=doc_type_keyboard()
        )

    elif state == WAITING_BACK:
        doc_type = context.user_data.get("doc_type", "ID Document")
        try:
            await generate_and_send_pdf(context.bot, chat_id,
                                        context.user_data["front"], pil_img, doc_type)
        except Exception as e:
            logger.error(f"PDF error: {e}")
            await update.message.reply_text("Error generating PDF. Use /reset and try again.")
        finally:
            context.user_data.clear()

    else:
        await update.message.reply_text("Use /start to begin.")


async def handle_doc_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("dt:"):
        return

    uid      = update.effective_user.id
    doc_type = query.data[3:]
    context.user_data["doc_type"] = doc_type

    # Check if this is for an album already in flight
    for mgid, group in media_groups.items():
        if group["uid"] == uid:
            # store doc_type so process_media_group can pick it up
            context.user_data["doc_type"] = doc_type
            await query.edit_message_text(
                f"Document type: *{doc_type}*\n\nProcessing your album...",
                parse_mode="Markdown"
            )
            return

    # Single-photo flow: move to waiting back
    context.user_data["state"] = WAITING_BACK
    await query.edit_message_text(
        f"Document type: *{doc_type}*\n\nNow send the *back* photo of the ID.",
        parse_mode="Markdown"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state", WAITING_FRONT)
    msgs = {
        WAITING_FRONT:    "Send the *front* photo of your ID to begin.",
        WAITING_DOC_TYPE: "Please tap a button above to select the document type.",
        WAITING_BACK:     "Please send the *back* photo of your ID.",
    }
    await update.message.reply_text(
        msgs.get(state, "Use /start to begin."),
        parse_mode="Markdown"
    )


# ── App setup ─────────────────────────────────────────────────────────────────

app = (
    ApplicationBuilder()
    .token(BOT_TOKEN)
    .build()
)

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("reset", reset))
app.add_handler(CallbackQueryHandler(handle_doc_type, pattern="^dt:"))
app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

logger.info("Bot is running...")
app.run_polling()
