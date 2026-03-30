import os
import json
import logging
from io import BytesIO
from pypdf import PdfWriter, PdfReader
from PIL import Image
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8791561190:AAEhUy4CwVyWJZgnM21LRqSgLEJd5InjdbY")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "1486225152"))

STAFF_FILE = "staff.json"

def load_staff() -> set:
    try:
        with open(STAFF_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_staff(staff: set):
    with open(STAFF_FILE, "w") as f:
        json.dump(list(staff), f)

allowed_ids: set = load_staff()

def is_authorized(uid: int) -> bool:
    return uid == ADMIN_ID or uid in allowed_ids

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

    # Card size: 10cm × 8cm
    CM_TO_PT = 72 / 2.54
    card_w = 10 * CM_TO_PT  # 283 pt
    card_h = 8  * CM_TO_PT  # 227 pt

    usable_w = A4_W - 2 * MARGIN

    x_start = MARGIN + (usable_w - card_w) / 2

    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)

    def draw_card(pil_img, y_center):
        img_buf = BytesIO()
        pil_img.save(img_buf, format="JPEG", quality=92)
        img_buf.seek(0)
        c.drawImage(ImageReader(img_buf), x_start, y_center - card_h / 2,
                    width=card_w, height=card_h, preserveAspectRatio=True, anchor="c")

    # Front centered in top half, back centered in bottom half
    draw_card(front, A4_H * 3 / 4)
    draw_card(back,  A4_H * 1 / 4)

    c.save()
    buf.seek(0)
    return buf


def image_to_a4_pdf(pil_img: Image.Image) -> bytes:
    """Convert a PIL image to a single-page A4 PDF, fitted to fill A4 in correct proportion."""
    A4_W, A4_H = A4
    buf = BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    img_buf = BytesIO()
    pil_img.convert("RGB").save(img_buf, format="JPEG", quality=92)
    img_buf.seek(0)
    c.drawImage(ImageReader(img_buf), 0, 0, width=A4_W, height=A4_H,
                preserveAspectRatio=True, anchor="c")
    c.save()
    buf.seek(0)
    return buf.read()


# ── Helpers ───────────────────────────────────────────────────────────────────

def doc_type_keyboard(prefix="dt"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Aadhaar",          callback_data=f"{prefix}:Aadhaar"),
         InlineKeyboardButton("Voter ID",          callback_data=f"{prefix}:Voter ID")],
        [InlineKeyboardButton("PAN",               callback_data=f"{prefix}:PAN"),
         InlineKeyboardButton("Passport",          callback_data=f"{prefix}:Passport")],
        [InlineKeyboardButton("Driving Licence",   callback_data=f"{prefix}:Driving Licence"),
         InlineKeyboardButton("Other",             callback_data=f"{prefix}:Other")],
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


# ── Admin commands ───────────────────────────────────────────────────────────

async def addstaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addstaff <telegram_id>")
        return
    try:
        new_id = int(context.args[0])
        allowed_ids.add(new_id)
        save_staff(allowed_ids)
        await update.message.reply_text(f"Staff {new_id} added successfully.")
    except ValueError:
        await update.message.reply_text("Invalid ID. Must be a number.")

async def removestaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removestaff <telegram_id>")
        return
    try:
        rem_id = int(context.args[0])
        allowed_ids.discard(rem_id)
        save_staff(allowed_ids)
        await update.message.reply_text(f"Staff {rem_id} removed.")
    except ValueError:
        await update.message.reply_text("Invalid ID. Must be a number.")

async def liststaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Not authorized.")
        return
    if not allowed_ids:
        await update.message.reply_text("No staff added yet.")
        return
    lines = "\n".join(str(i) for i in allowed_ids)
    await update.message.reply_text(f"Authorized staff:\n{lines}")


# ── Handlers ──────────────────────────────────────────────────────────────────

STAFF_GUIDE = (
    "📋 *ID Document Layout Bot — Quick Guide*\n\n"
    "──────────────────────\n"
    "🪪 *Make ID Card PDF*\n"
    "──────────────────────\n"
    "*Recommended — use /id (doc type first):*\n"
    "1. Send /id\n"
    "2. Tap document type button\n"
    "3. Send front + back as album (or one by one)\n"
    "4. Receive your PDF ✅\n\n"
    "*Quick — send photos directly:*\n"
    "• Album: send front + back together → tap doc type\n"
    "• One by one: front photo → pick type → back photo\n\n"
    "💡 Tip: Crop photo to just the card before sending\n\n"
    "──────────────────────\n"
    "📎 *Merge Files*\n"
    "──────────────────────\n"
    "1. Send /merge\n"
    "2. Send PDFs, JPEGs, or PNGs one by one\n"
    "   (images become full A4 pages)\n"
    "3. Send /done → receive merged PDF ✅\n"
    "   Send /cancel to abort\n\n"
    "──────────────────────\n"
    "⚙️ *Commands*\n"
    "──────────────────────\n"
    "/id    — Make ID card PDF (choose type first)\n"
    "/start — Show this guide\n"
    "/reset — Start over\n"
    "/merge — Start PDF merge\n"
    "/done  — Finish merging\n"
    "/cancel — Cancel merge"
)

ADMIN_EXTRA = (
    "\n\n"
    "──────────────────────\n"
    "🔐 *Admin Commands*\n"
    "──────────────────────\n"
    "/addstaff <id> — Add a staff member\n"
    "/removestaff <id> — Remove a staff member\n"
    "/liststaff — List all authorized staff"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    context.user_data.clear()
    guide = STAFF_GUIDE + (ADMIN_EXTRA if uid == ADMIN_ID else "")
    await update.message.reply_text(guide, parse_mode="Markdown")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "Reset done! Send front photo or an album of front+back.",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    if not is_authorized(uid):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    chat_id = update.effective_chat.id
    mgid    = update.message.media_group_id

    # ── Merge mode: photo becomes an A4 page ─────────────────────────────────
    if context.user_data.get("merge_mode"):
        try:
            pil_img   = await download_image(update.message.photo[-1])
            pdf_bytes = image_to_a4_pdf(pil_img)
            context.user_data["merge_pdfs"].append(pdf_bytes)
            count = len(context.user_data["merge_pdfs"])
            await update.message.reply_text(
                f"Image (converted to A4 page) {count} received. Send more or /done to merge."
            )
        except Exception as e:
            logger.error(f"Merge photo error: {e}")
            await update.message.reply_text("Could not process that photo. Try again.")
        return

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
        if context.user_data.get("id_flow"):
            # Doc type already chosen via /id — skip selection, go straight to back
            context.user_data["state"] = WAITING_BACK
            doc_type = context.user_data.get("doc_type", "ID Document")
            await update.message.reply_text(
                f"Front saved! Now send the *back* photo of the *{doc_type}*.",
                parse_mode="Markdown"
            )
        else:
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


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    context.user_data.clear()
    await update.message.reply_text(
        "Select the document type first:",
        reply_markup=doc_type_keyboard(prefix="id")
    )

async def handle_id_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    doc_type = query.data[3:]   # strip "id:"
    uid = update.effective_user.id
    context.user_data["doc_type"] = doc_type
    context.user_data["state"]    = WAITING_FRONT
    context.user_data["id_flow"]  = True
    await query.edit_message_text(
        f"Document type: *{doc_type}*\n\n"
        "Now send the photos:\n"
        "• *Both together* — select front + back and send as album\n"
        "• *One by one* — send front photo first",
        parse_mode="Markdown"
    )


async def merge_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    context.user_data["merge_mode"] = True
    context.user_data["merge_pdfs"] = []
    await update.message.reply_text(
        "Merge mode ON.\n\n"
        "Send files one by one:\n"
        "• PDF files\n"
        "• JPEG / PNG images (sent as files or photos)\n\n"
        "Each image becomes a full A4 page.\n"
        "Send /done to merge. Send /cancel to abort."
    )

async def merge_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    pdfs = context.user_data.get("merge_pdfs", [])
    if len(pdfs) < 2:
        await update.message.reply_text("Please send at least 2 PDF files before /done.")
        return
    await update.message.reply_text(f"Merging {len(pdfs)} PDFs, please wait...")
    try:
        writer = PdfWriter()
        for pdf_bytes in pdfs:
            reader = PdfReader(BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        out = BytesIO()
        writer.write(out)
        out.seek(0)
        await update.message.reply_document(
            document=out,
            filename="merged.pdf",
            caption=f"Merged {len(pdfs)} PDFs successfully!"
        )
    except Exception as e:
        logger.error(f"Merge error: {e}")
        await update.message.reply_text("Error merging PDFs. Try again.")
    finally:
        context.user_data.pop("merge_mode", None)
        context.user_data.pop("merge_pdfs", None)

async def merge_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    context.user_data.pop("merge_mode", None)
    context.user_data.pop("merge_pdfs", None)
    await update.message.reply_text("Merge cancelled.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
    if not context.user_data.get("merge_mode"):
        await update.message.reply_text("Send /merge to start merging PDFs.")
        return
    doc = update.message.document
    fname = (doc.file_name or "").lower()
    is_pdf   = fname.endswith(".pdf")
    is_image = fname.endswith((".jpg", ".jpeg", ".png"))
    if not is_pdf and not is_image:
        await update.message.reply_text("Only PDF, JPG, or PNG files are accepted.")
        return
    try:
        file      = await doc.get_file()
        raw_bytes = bytes(await file.download_as_bytearray())
        if is_image:
            pil_img  = Image.open(BytesIO(raw_bytes))
            pdf_bytes = image_to_a4_pdf(pil_img)
            label = "Image (converted to A4 page)"
        else:
            pdf_bytes = raw_bytes
            label = "PDF"
        context.user_data["merge_pdfs"].append(pdf_bytes)
        count = len(context.user_data["merge_pdfs"])
        await update.message.reply_text(f"{label} {count} received. Send more or /done to merge.")
    except Exception as e:
        logger.error(f"Merge file error: {e}")
        await update.message.reply_text("Could not read that file. Try again.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        return
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

app.add_handler(CommandHandler("start",        start))
app.add_handler(CommandHandler("id",           id_command))
app.add_handler(CommandHandler("reset",        reset))
app.add_handler(CommandHandler("addstaff",     addstaff))
app.add_handler(CommandHandler("removestaff",  removestaff))
app.add_handler(CommandHandler("liststaff",    liststaff))
app.add_handler(CommandHandler("merge",        merge_start))
app.add_handler(CommandHandler("done",         merge_done))
app.add_handler(CommandHandler("cancel",       merge_cancel))
app.add_handler(CallbackQueryHandler(handle_id_select,  pattern="^id:"))
app.add_handler(CallbackQueryHandler(handle_doc_type,   pattern="^dt:"))
app.add_handler(MessageHandler(filters.PHOTO,                       handle_photo))
app.add_handler(MessageHandler(filters.Document.PDF,                handle_document))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,     handle_text))

async def set_commands(app):
    await app.bot.set_my_commands([
        ("id",          "Make ID card PDF — choose doc type first"),
        ("start",       "Show instructions"),
        ("reset",       "Reset and start over"),
        ("merge",       "Merge PDFs and images (JPG/PNG) into one PDF"),
        ("done",        "Finish merging and get PDF"),
        ("cancel",      "Cancel current merge"),
        ("liststaff",   "List authorized staff (admin only)"),
        ("addstaff",    "Add a staff member (admin only)"),
        ("removestaff", "Remove a staff member (admin only)"),
    ])

app.post_init = set_commands

logger.info("Bot is running...")
app.run_polling()
