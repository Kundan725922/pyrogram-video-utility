import os
import asyncio
import re
from functools import wraps

from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from pyrogram import Client, errors
from pyrogram.file_id import FileId, FileType
from pyrogram.raw.functions.messages import GetMessages
from pyrogram.raw.types import InputMessageID, InputPeerChannel

# --- Configuration and Initialization ---

app = Flask(__name__)
# Enable CORS for all routes (important for web usage)
CORS(app)

# Environment variables
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_STRING = os.getenv("PYROGRAM_SESSION")

# Telegram Client Global Variable
telegram_client = None

def client_initializer():
    """Initializes and starts the Pyrogram client."""
    global telegram_client
    
    # Client name based on session type
    client_name = "render_bot" if BOT_TOKEN else "render_session"

    if SESSION_STRING and API_ID and API_HASH:
        # Use session string if available
        telegram_client = Client(
            name=client_name,
            api_id=int(API_ID),
            api_hash=API_HASH,
            session_string=SESSION_STRING,
            workdir="./pyrogram_session_files" # Use a dedicated folder
        )
        print("Client starting using PYROGRAM_SESSION environment variable.")
    elif BOT_TOKEN and API_ID and API_HASH:
        # Fallback to bot token if session string is missing
        telegram_client = Client(
            name=client_name,
            api_id=int(API_ID),
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        print("Client starting as a BOT using BOT_TOKEN environment variable.")
    else:
        print("FATAL: Missing one or more required environment variables (API_ID, API_HASH).")
        return

    try:
        # Start the client synchronously before Gunicorn boots workers
        telegram_client.start()
        print("Pyrogram Client started successfully!")
    except Exception as e:
        print(f"Error starting Pyrogram client (FATAL): {e}")
        telegram_client = None

# Run the client initialization once when the module loads
client_initializer()


# --- Utility Functions ---

def require_auth(f):
    """Decorator to enforce a simple token-based authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_token = request.args.get('token')
        # Replace 'YourSecretKey' with your actual secure key environment variable
        SECRET_TOKEN = os.getenv("SECRET_AUTH_TOKEN", "MySecretKeyForTrustedRkuser@143")
        
        if auth_token != SECRET_TOKEN:
            return jsonify({"error": "Unauthorized access. Invalid or missing 'token' parameter."}), 401
        return f(*args, **kwargs)
    return decorated_function

def parse_telegram_url(url):
    """
    Parses a Telegram t.me URL to extract chat_id and message_id.
    Supports public and private channel links (t.me/c/chat_id/message_id).
    """
    match = re.match(r"https?://t\.me/c/(\d+)/(\d+)", url)
    if match:
        # Private/Supergroup channel link: chat_id is channel_id (not peer_id)
        # Pyrogram automatically handles the conversion from channel_id (e.g., 1345678901) 
        # to the required Telegram peer ID (-1001345678901).
        channel_id = int(match.group(1))
        message_id = int(match.group(2))
        return channel_id, message_id
    
    # Add support for public channel/username links if needed, but private is the main focus
    # For simplicity, we only handle the private link format here.
    return None, None

def run_async(coro):
    """Helper function to run an async coroutine synchronously."""
    # This is essential when running async Pyrogram calls inside Flask's sync workers
    return asyncio.run(coro)

# --- Pyrogram/Telegram API Helpers ---

async def get_telegram_video_file_id(chat_id, message_id):
    """Fetches the file_id of a video message from Telegram."""
    if not telegram_client:
        return None, "Telegram client not initialized."
    
    try:
        # Get the message using the full message object
        message = await telegram_client.get_messages(chat_id, message_ids=message_id)

        if not message:
            return None, "Message not found."
        
        # Check if the message contains a video
        if message.video:
            # We need the full FileId object for streaming
            # video.file_id is a string, which Pyrogram can handle automatically 
            # for download, but having the full object is good practice.
            return message.video.file_id, None
        
        return None, "Message does not contain a video."
        
    except errors.ChannelInvalid as e:
        # Common error if the channel link is wrong or bot/user is not a member
        return None, f"Channel Invalid: The bot/user is not a member of the channel or the chat_id is incorrect. Ensure the channel is linked correctly. ({e})"
    except errors.MessageNotModified as e:
        # Another error that can happen if the message ID is bad
        return None, f"Pyrogram Error: Message Not Modified. Check chat ID and message ID. ({e})"
    except Exception as e:
        print(f"Unexpected Pyrogram Error: {e}")
        return None, f"An unexpected error occurred while fetching the message: {e}"


# --- Flask Routes ---

@app.route('/', methods=['GET'])
def index():
    """Returns a simple usage guide."""
    return jsonify({
        "status": "Pyrogram Video Utility is running",
        "endpoints": {
            "stream": "/stream-telegram-video?url=<telegram_link>&token=<auth_token>",
            "download": "/download-telegram-video?url=<telegram_link>&token=<auth_token>",
        },
        "url_format": "https://t.me/c/<channel_id>/<message_id>",
        "notes": [
            "You MUST set the environment variable SECRET_AUTH_TOKEN to match the 'token' in the URL.",
            "The Telegram client MUST be a member of the private channel.",
            "Ensure Bot Privacy Mode is DISABLED via @BotFather if using a bot token."
        ]
    })


@app.route('/stream-telegram-video', methods=['GET'])
@require_auth
def stream_video():
    """
    Streams a Telegram video file using Pyrogram's file_ref stream functionality.
    This mimics a progressive download, suitable for HTML5 video players.
    """
    video_url = request.args.get('url')
    
    if not video_url:
        return jsonify({"error": "Missing 'url' parameter."}), 400

    # FIX: Remove the deprecated 'telegram_client.is_running' check.
    # The client is started once before Flask starts, so we only check if it initialized.
    if not telegram_client:
        return jsonify({"error": "Telegram client failed to initialize. Check server logs."}), 503

    chat_id, message_id = parse_telegram_url(video_url)

    if not chat_id or not message_id:
        return jsonify({"error": "Invalid Telegram URL format. Use https://t.me/c/<channel_id>/<message_id>"}), 400

    # Step 1: Get the video file_id (which includes the file_ref)
    file_id_string, error = run_async(get_telegram_video_file_id(chat_id, message_id))

    if error:
        return jsonify({"error": error}), 500
    if not file_id_string:
        return jsonify({"error": "Could not retrieve file ID from Telegram."}), 500

    # Get the file size for proper HTTP Range handling
    try:
        # Use FileId.decode to get the object and then get_file
        # Pyrogram's get_file can be used for metadata, but using get_messages is often simpler.
        message = run_async(telegram_client.get_messages(chat_id, message_ids=message_id))
        video_size = message.video.file_size
    except Exception as e:
        print(f"Error retrieving video size: {e}")
        return jsonify({"error": f"Error retrieving video size/metadata: {e}"}), 500


    # Step 2: Set up streaming handler
    range_header = request.headers.get('Range', 'bytes=0-')
    match = re.search(r'bytes=(\d+)-(\d*)', range_header)
    
    if match:
        start_byte = int(match.group(1))
        end_byte_str = match.group(2)
        end_byte = int(end_byte_str) if end_byte_str else video_size - 1
    else:
        start_byte = 0
        end_byte = video_size - 1

    length = end_byte - start_byte + 1

    def generate():
        """Generator to stream chunks of the file."""
        # Use a large chunk size for performance
        chunk_size = 1024 * 1024 * 4 # 4MB
        
        # Determine how many chunks to read
        current_offset = start_byte
        
        while current_offset <= end_byte:
            remaining_bytes = end_byte - current_offset + 1
            read_size = min(chunk_size, remaining_bytes)
            
            try:
                # Use Pyrogram's stream_media for range-based reading
                # Note: stream_media returns an async generator
                async def stream_chunk():
                    async for chunk in telegram_client.stream_media(
                        file_id_string,
                        offset=current_offset,
                        limit=read_size,
                        chunk_size=chunk_size # This internal Pyrogram chunk size must be honored
                    ):
                        yield chunk
                
                # Execute the async generator and yield the resulting chunks
                for chunk in run_async(stream_chunk()):
                    yield chunk
                    current_offset += len(chunk)
            
            except Exception as e:
                # Handle streaming errors gracefully
                print(f"Streaming Error: {e}")
                break

    # Construct the Response object with appropriate headers for streaming
    if start_byte != 0 or end_byte != video_size - 1:
        # Partial Content response (HTTP 206)
        status_code = 206
        headers = {
            'Content-Range': f'bytes {start_byte}-{end_byte}/{video_size}',
            'Content-Length': str(length)
        }
    else:
        # Full Content response (HTTP 200)
        status_code = 200
        headers = {
            'Content-Length': str(video_size)
        }
        
    # Standard streaming headers
    headers.update({
        'Content-Type': 'video/mp4', # Assuming standard Telegram video is mp4
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
    })
    
    return Response(generate(), status=status_code, headers=headers)


@app.route('/download-telegram-video', methods=['GET'])
@require_auth
def download_video():
    """
    Triggers a full download of the Telegram video file.
    This is less efficient for large files and streaming players than /stream-telegram-video.
    """
    video_url = request.args.get('url')

    if not video_url:
        return jsonify({"error": "Missing 'url' parameter."}), 400

    # FIX: Remove the deprecated 'telegram_client.is_running' check.
    if not telegram_client:
        return jsonify({"error": "Telegram client failed to initialize. Check server logs."}), 503

    chat_id, message_id = parse_telegram_url(video_url)

    if not chat_id or not message_id:
        return jsonify({"error": "Invalid Telegram URL format. Use https://t.me/c/<channel_id>/<message_id>"}), 400

    # Step 1: Get the video file_id
    file_id_string, error = run_async(get_telegram_video_file_id(chat_id, message_id))

    if error:
        return jsonify({"error": error}), 500
    if not file_id_string:
        return jsonify({"error": "Could not retrieve file ID from Telegram."}), 500

    # Step 2: Download the file to a temporary location
    try:
        # Pyrogram's download_media is a convenient way to get the file
        temp_file_path = run_async(telegram_client.download_media(
            file_id_string,
            file_name=f"downloads/{chat_id}_{message_id}"
        ))
        
        if not temp_file_path:
             return jsonify({"error": "Download failed, file path is empty."}), 500

        # Step 3: Send the file and clean up
        return send_file(
            temp_file_path,
            as_attachment=True,
            download_name=f"video-{chat_id}-{message_id}.mp4"
        )
        
    except Exception as e:
        print(f"Download Error: {e}")
        return jsonify({"error": f"An error occurred during download: {e}"}), 500
    finally:
        # Clean up the temporary file *after* sending it
        if 'temp_file_path' in locals() and os.path.exists(temp_file_path):
            os.remove(temp_file_path)


# Ensure the downloads directory exists for the download function
if not os.path.exists("downloads"):
    os.makedirs("downloads")

if __name__ == '__main__':
    # When running locally (not via gunicorn), use the app.run() method
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), debug=True)
