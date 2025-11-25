import os
import re
from flask import Flask, request, Response, send_from_directory 
from pyrogram import Client
from pyrogram.errors import MessageIdInvalid, UsernameInvalid, UserNotParticipant
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant 
from flask_cors import CORS 

# --- Configuration (Hardcoded values are safe here) ---
# Your API credentials
API_ID = 35172395
API_HASH = "3cb710c4a835a23eeb73112026d46686"

# Fetch tokens from environment variables (Correctly fetches by NAME)
BOT_TOKEN_ENV = os.environ.get("BOT_TOKEN") 
SESSION_STRING_ENV = os.environ.get("PYROGRAM_SESSION")
SERVER_ACCESS_TOKEN = os.environ.get("SERVER_ACCESS_TOKEN")

# Pyrogram client will be initialized here
telegram_client = None

app = Flask(__name__)
CORS(app) 

# --- PYROGRAM CLIENT STARTUP (Supports BOTH Bot and User Session) ---
# Check if we are running in the main Flask process (not the reloader process)
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    try:
        # 1. PRIORITIZE BOT TOKEN (Recommended for reliable private chat access)
        if BOT_TOKEN_ENV:
            client_name = "bot_streamer"
            telegram_client = Client(
                client_name, 
                API_ID, 
                API_HASH, 
                bot_token=BOT_TOKEN_ENV  # <--- Use Bot Token
            )
            print("Client starting as a BOT using BOT_TOKEN environment variable.")
        # 2. FALLBACK to User Session String
        elif SESSION_STRING_ENV:
            client_name = "user_streamer" 
            telegram_client = Client(
                client_name, 
                API_ID, 
                API_HASH, 
                session_string=SESSION_STRING_ENV # <--- Use User Session String
            )
            print("Client starting as USER using PYROGRAM_SESSION environment variable.")
        else:
            raise Exception("FATAL: Neither BOT_TOKEN nor PYROGRAM_SESSION is configured.")
            
        # Start the client
        telegram_client.start()
        print("Pyrogram Client started successfully!")

    except Exception as e:
        print(f"Error starting Pyrogram client (FATAL): {e}")
        print("ACTION: Check API_ID/API_HASH/BOT_TOKEN/PYROGRAM_SESSION configuration.")
else:
    # This runs in the reloader process, where we explicitly skip Pyrogram startup
    print("Pyrogram client startup skipped in Flask reloader process.")
# -------------------------------------------------------------------


# --- FIX: Route to serve the HTML file (Frontend) ---
@app.route('/')
def serve_frontend():
    """Serves the telegram_vedio_streammer.html file from the current directory."""
    # This fixes the 404 Not Found error by correctly locating and serving your HTML file
    return send_from_directory(os.getcwd(), 'telegram_vedio_streammer.html')

# --- Utility Function to Parse Telegram Link ---
def parse_link(url):
    """Parses t.me/c/channel_id/message_id (private) or t.me/username/message_id (public)."""
    # Regex to extract channel ID/username and message ID
    match = re.search(r't\.me/(?:c/)?([a-zA-Z0-9_-]+)/(\d+)', url)
    if match:
        chat_identifier_part = match.group(1)
        message_id = int(match.group(2))

        if url.find("/c/") != -1:
            # Private channel ID needs the -100 prefix for Pyrogram
            try:
                # IMPORTANT: Pyrogram uses -100 prefix for channel IDs
                # Your log shows ID 3384286590, so the chat_id should be -1003384286590
                chat_identifier = int('-100' + chat_identifier_part)
            except ValueError:
                return None, None
        else:
            # Public channel username needs the @ prefix
            chat_identifier = "@" + chat_identifier_part 

        return chat_identifier, message_id
    return None, None

# --- Streaming Endpoint ---
@app.route('/stream-telegram-video', methods=['GET'])
def stream_video():
    # Check for authentication token
    requested_token = request.args.get('token')
    if SERVER_ACCESS_TOKEN and requested_token != SERVER_ACCESS_TOKEN:
        return {"error": "Invalid access token."}, 401

    # We must ensure the client is running before proceeding
    if not telegram_client or not telegram_client.is_running:
        return {"error": "Telegram client is not running. Check server logs."}, 503

    telegram_url = request.args.get('url')
    if not telegram_url:
        return {"error": "Missing 'url' parameter."}, 400

    chat_id, message_id = parse_link(telegram_url)

    if not chat_id or not message_id:
        return {"error": "Invalid Telegram URL format. Use t.me/c/ID/MSG or t.me/USERNAME/MSG."}, 400

    try:
        # 1. Get the message containing the media
        message = telegram_client.get_messages(chat_id, message_ids=message_id)

        if isinstance(message, list):
            message = message[0] if message else None

        if not message or not message.video:
            return {"error": "Message does not contain a video or was not found. Check if the link is correct."}, 404

        file_size = message.video.file_size
        file_name = message.video.file_name

        # 2. Pyrogram's generator function to stream the file data in chunks
        def generate_stream():
            try:
                # stream_media fetches the file in small chunks suitable for streaming
                for chunk in telegram_client.stream_media(message.video):
                    yield chunk
            except Exception as e:
                print(f"Streaming error during generation: {e}")

        # 3. Create a Flask Response object for streaming
        response = Response(
            generate_stream(), 
            mimetype='video/mp4', # Crucial for browser video player compatibility
            content_type='video/mp4'
        )
        
        # Add necessary headers for the browser to stream and seek video content
        response.headers['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response.headers['Content-Length'] = str(file_size)
        response.headers['Accept-Ranges'] = 'bytes'
        
        response.status_code = 200 

        return response

    except UserNotParticipant:
        return {"error": "The authenticated account is not a member of this private chat or channel."}, 403
    except MessageIdInvalid:
        return {"error": "Invalid message ID or chat ID. Could not locate message."}, 404
    except UsernameInvalid:
        return {"error": "Invalid chat username or channel ID format."}, 404
    except Exception as e:
        # The FATAL STREAMING SERVER ERROR: Peer id invalid will be caught here
        print(f"FATAL STREAMING SERVER ERROR: {e}") 
        return {"error": f"An unexpected server error occurred: {e}"}, 500

# --- Download Endpoint (Copying the logic from streaming, but for download) ---
@app.route('/download-telegram-video', methods=['GET'])
def download_video():
    # Check for authentication token (re-used logic for the download endpoint)
    requested_token = request.args.get('token')
    if SERVER_ACCESS_TOKEN and requested_token != SERVER_ACCESS_TOKEN:
        return {"error": "Invalid access token."}, 401
    
    # We must ensure the client is running before proceeding
    if not telegram_client or not telegram_client.is_running:
        return {"error": "Telegram client is not running. Check server logs."}, 503

    telegram_url = request.args.get('url')
    if not telegram_url:
        return {"error": "Missing 'url' parameter."}, 400

    chat_id, message_id = parse_link(telegram_url)

    if not chat_id or not message_id:
        return {"error": "Invalid Telegram URL format. Use t.me/c/ID/MSG or t.me/USERNAME/MSG."}, 400

    try:
        # 1. Get the message containing the media
        message = telegram_client.get_messages(chat_id, message_ids=message_id)

        if isinstance(message, list):
            message = message[0] if message else None

        if not message or not message.video:
            return {"error": "Message does not contain a video or was not found. Check if the link is correct."}, 404

        file_size = message.video.file_size
        file_name = message.video.file_name

        # 2. Pyrogram's generator function to stream the file data in chunks
        def generate_download():
            try:
                # stream_media fetches the file in small chunks suitable for streaming
                for chunk in telegram_client.stream_media(message.video):
                    yield chunk
            except Exception as e:
                print(f"Download error during generation: {e}")

        # 3. Create a Flask Response object for streaming/downloading
        response = Response(
            generate_download(), 
            mimetype='application/octet-stream', # Forces download
            content_type='application/octet-stream'
        )
        
        # Add necessary headers to prompt a file download
        response.headers['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response.headers['Content-Length'] = str(file_size)
        response.status_code = 200 

        return response

    except UserNotParticipant:
        return {"error": "The authenticated account is not a member of this private chat or channel."}, 403
    except MessageIdInvalid:
        return {"error": "Invalid message ID or chat ID. Could not locate message."}, 404
    except UsernameInvalid:
        return {"error": "Invalid chat username or channel ID format."}, 404
    except Exception as e:
        # The FATAL DOWNLOAD SERVER ERROR: Peer id invalid will be caught here
        print(f"FATAL DOWNLOAD SERVER ERROR: {e}")
        return {"error": f"An unexpected server error occurred: {e}"}, 500

# --- Start the server ---
if __name__ == '__main__':
    # This runs the server when executed locally
    app.run(debug=True, port=8000)
